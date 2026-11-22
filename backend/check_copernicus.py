"""Safely verify CDSE client-credentials OAuth without printing secrets or tokens."""

import asyncio
import httpx
from app.config import settings

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"


async def main() -> None:
    if not settings.cdse_client_id or not settings.cdse_client_secret:
        print("Copernicus credentials are missing. Set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET in backend/.env.")
        return

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.cdse_client_id,
                    "client_secret": settings.cdse_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.ConnectError:
        print("Could not connect to the Copernicus identity service. Check DNS, proxy, and outbound HTTPS.")
        return
    except httpx.TimeoutException:
        print("Connection to Copernicus timed out. Check proxy/firewall and try again.")
        return
    except httpx.HTTPError as exc:
        print(f"Copernicus request failed ({type(exc).__name__}). Check backend network settings and retry.")
        return

    if response.is_success:
        print("Copernicus OAuth succeeded. Credentials are valid and the client-credentials flow is working.")
        return

    if response.status_code in (400, 401):
        print(f"Copernicus rejected the OAuth request (HTTP {response.status_code}). Re-copy the client ID and secret; confirm Client Credentials flow and that the client has not expired.")
    else:
        print(f"Copernicus OAuth returned HTTP {response.status_code}. Check the Copernicus service status and account quota, then retry.")


if __name__ == "__main__":
    asyncio.run(main())
