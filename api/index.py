import os
import re
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from curl_cffi import requests

app = FastAPI(title="TeraBox Proxy Resolver")

# Enable CORS so your Cloudflare Worker or web client can access it directly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default Webshare proxy (can be overwritten via PROXY_URL env variable in Vercel dashboard)
DEFAULT_PROXY = "http://tbuombnh:4pd2nzus99xj@38.154.203.95:5863/"

@app.get("/")
def read_root():
    return {"status": "ok", "message": "TeraBox Vercel Resolver is running."}

@app.get("/api/test-proxy")
def test_proxy():
    import os
    proxy_url = os.environ.get("PROXY_URL", DEFAULT_PROXY)
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }
    
    test_url = "https://www.terabox.app/sharing/link?surl=zghhcVThYPRRJ3ciFocryg"
    
    # Try using curl_cffi
    try:
        r = requests.get(test_url, proxies=proxies, impersonate="chrome120", timeout=10)
        return {
            "success": True,
            "status_code": r.status_code,
            "html_length": len(r.text),
            "need_verify": "need verify" in r.text,
            "library": "curl_cffi"
        }
    except Exception as e:
        curl_cffi_err = str(e)
        
    # Try using standard urllib
    try:
        import urllib.request
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(test_url, headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=10) as response:
            html = response.read().decode('utf-8')
            return {
                "success": True,
                "html_length": len(html),
                "need_verify": "need verify" in html,
                "library": "urllib",
                "curl_cffi_error": curl_cffi_err
            }
    except Exception as e:
        return {
            "success": False,
            "curl_cffi_error": curl_cffi_err,
            "urllib_error": str(e)
        }

@app.get("/api/resolve")
def resolve_share(surl: str = Query(...), ndus: str = Query(None)):
    # 1. Normalize surl (remove leading '1' if it's 23 characters long)
    if surl and len(surl) == 23 and surl.startswith("1"):
        surl = surl[1:]
        
    # 2. Load proxy configuration
    proxy_url = os.environ.get("PROXY_URL", DEFAULT_PROXY)
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }
    
    # 3. Setup cookies (TeraBox auth)
    # ndus can be passed via query parameter, or it will fall back to NDUS env variable
    active_ndus = ndus or os.environ.get("NDUS")
    cookies = {}
    if active_ndus:
        cookies["ndus"] = active_ndus

    session = requests.Session()
    session.proxies = proxies
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.terabox.app/"
    }
    
    # 4. Fetch share page to extract jsToken
    page_url = f"https://www.terabox.app/sharing/link?surl={surl}"
    r_page = None
    used_ndus = False
    ndus_error = None
    ndus_status = None
    
    if cookies:
        try:
            r_page = session.get(
                page_url, 
                headers=headers, 
                cookies=cookies, 
                impersonate="chrome120", 
                timeout=10
            )
            ndus_status = r_page.status_code
            if r_page.status_code == 200 and "need verify" not in r_page.text:
                used_ndus = True
            elif r_page.status_code == 200:
                ndus_error = "need verify in text"
            else:
                ndus_error = f"HTTP {r_page.status_code}"
        except Exception as e:
            ndus_error = str(e)
            
    if r_page is None or r_page.status_code != 200 or "need verify" in r_page.text:
        try:
            # Clear cookies and retry anonymously
            session.cookies.clear()
            r_page = session.get(
                page_url, 
                headers=headers, 
                cookies={}, 
                impersonate="chrome120", 
                timeout=10
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch TeraBox page (anonymous retry): {str(e)}")
        
    if r_page.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TeraBox page returned HTTP {r_page.status_code}")
        
    html = r_page.text
    if "need verify" in html:
        raise HTTPException(
            status_code=403, 
            detail={
                "error": "need verify", 
                "code": "upstream_verification_required",
                "message": "TeraBox page requires verification even without cookies."
            }
        )
        
    # Match fn%28%22<token>%22%29 or fn%28%27<token>%27%29 or raw function calls to get the jsToken
    match = re.search(r'fn(?:%28|\()(?:%22|%27|["\'])([^%\'"\(\)]+)(?:%22|%27|["\'])(?:%29|\))', html)
    if not match:
        raise HTTPException(status_code=422, detail="Failed to extract jsToken from page HTML")
        
    js_token = match.group(1)
    
    # 5. Fetch share list API
    api_url = f"https://dm.terabox.app/share/list?jsToken={js_token}&shorturl={surl}&root=1"
    api_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.terabox.app/"
    }
    
    r_api = None
    if used_ndus:
        try:
            r_api = session.get(
                api_url, 
                headers=api_headers, 
                cookies=cookies, 
                impersonate="chrome120", 
                timeout=10
            )
        except Exception as e:
            print(f"API fetch with ndus failed: {e}. Retrying anonymously.")
            
    if r_api is None or r_api.status_code != 200:
        try:
            session.cookies.clear()
            r_api = session.get(
                api_url, 
                headers=api_headers, 
                cookies={}, 
                impersonate="chrome120", 
                timeout=10
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to call TeraBox API (anonymous retry): {str(e)}")
        
    if r_api.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TeraBox API returned HTTP {r_api.status_code}")
        
    try:
        res_json = r_api.json()
    except Exception:
        raise HTTPException(status_code=502, detail="TeraBox API returned non-JSON response")
        
    # Check if the API output itself returned verification challenge
    if res_json.get("errno") in [4000020, 400141] or "need verify" in str(res_json.get("errmsg", "")):
        # If API failed with verify, try once more fully anonymous
        if used_ndus:
            try:
                session.cookies.clear()
                r_api_anon = session.get(
                    api_url, 
                    headers=api_headers, 
                    cookies={}, 
                    impersonate="chrome120", 
                    timeout=10
                )
                if r_api_anon.status_code == 200:
                    res_anon_json = r_api_anon.json()
                    if res_anon_json.get("errno") not in [4000020, 400141]:
                        return res_anon_json
            except Exception:
                pass
                
        raise HTTPException(
            status_code=403, 
            detail={
                "error": "need verify", 
                "code": "upstream_verification_required",
                "details": res_json
            }
        )
        
    # Inject debug resolver metadata to check fallback status
    res_json["debug_resolver"] = {
        "used_ndus": used_ndus,
        "ndus_length": len(active_ndus) if active_ndus else 0,
        "ndus_error": ndus_error,
        "ndus_status": ndus_status
    }
    return res_json
