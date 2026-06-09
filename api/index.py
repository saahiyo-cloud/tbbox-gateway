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
    
    # Try using curl_cffi
    try:
        r = requests.get("https://ipv4.webshare.io/", proxies=proxies, timeout=10)
        return {
            "success": True,
            "status_code": r.status_code,
            "ip": r.text.strip(),
            "library": "curl_cffi"
        }
    except Exception as e:
        curl_cffi_err = str(e)
        
    # Try using standard urllib as a fallback to see if it's a curl_cffi specific issue
    try:
        import urllib.request
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open("https://ipv4.webshare.io/", timeout=10) as response:
            ip = response.read().decode('utf-8').strip()
            return {
                "success": True,
                "ip": ip,
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
    try:
        r_page = session.get(
            page_url, 
            headers=headers, 
            cookies=cookies, 
            impersonate="chrome120", 
            timeout=10
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch TeraBox page: {str(e)}")
        
    if r_page.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TeraBox page returned HTTP {r_page.status_code}")
        
    html = r_page.text
    if "need verify" in html:
        raise HTTPException(
            status_code=403, 
            detail={
                "error": "need verify", 
                "code": "upstream_verification_required",
                "message": "The proxy IP or ndus cookie has triggered a verification challenge."
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
    
    try:
        r_api = session.get(
            api_url, 
            headers=api_headers, 
            cookies=cookies, 
            impersonate="chrome120", 
            timeout=10
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to call TeraBox API: {str(e)}")
        
    if r_api.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TeraBox API returned HTTP {r_api.status_code}")
        
    try:
        res_json = r_api.json()
    except Exception:
        raise HTTPException(status_code=502, detail="TeraBox API returned non-JSON response")
        
    # Check if the API output itself returned verification challenge
    if res_json.get("errno") in [4000020, 400141] or "need verify" in str(res_json.get("errmsg", "")):
        raise HTTPException(
            status_code=403, 
            detail={
                "error": "need verify", 
                "code": "upstream_verification_required",
                "details": res_json
            }
        )
        
    return res_json
