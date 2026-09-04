Place your SSL certificate files here:

  fullchain.pem  — your full certificate chain
  privkey.pem    — your private key

──────────────────────────────────────────────────────
OPTION A: Free SSL with Let's Encrypt (recommended)
──────────────────────────────────────────────────────
1. Install certbot on your server:
     sudo apt install certbot python3-certbot-nginx

2. Temporarily allow HTTP in docker-compose.yml (it already is)

3. Run:
     certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com

4. Copy certs to this folder:
     cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ./fullchain.pem
     cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   ./privkey.pem

5. Renew automatically (certbot does this, then copy again):
     certbot renew

──────────────────────────────────────────────────────
OPTION B: Self-signed (local dev / testing only)
──────────────────────────────────────────────────────
Run this command in this folder:
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout privkey.pem -out fullchain.pem \
    -subj "/CN=localhost"

WARNING: Self-signed certs show browser warnings. Never use in production.
