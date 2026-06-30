$urls = @(
    'http://127.0.0.1:8000/',
    'http://127.0.0.1:8000/products/',
    'http://127.0.0.1:8000/cart/',
    'http://127.0.0.1:8000/login/',
    'http://127.0.0.1:8000/panel/'
)
foreach ($url in $urls) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -Method Get -TimeoutSec 10
        Write-Output "$url -> $($resp.StatusCode)"
    } catch {
        Write-Output "$url -> ERROR: $($_.Exception.Message)"
    }
}
