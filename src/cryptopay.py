import httpx
import logging
from src import config

log = logging.getLogger(__name__)

class CryptoPayAPI:
    def __init__(self, api_token: str, base_url: str):
        if not api_token:
            raise ValueError("Crypto Pay API token is required.")
        if not base_url:
            raise ValueError("Crypto Pay API base URL is required.")
        self.api_token = api_token
        self.base_url = base_url
        self.headers = {
            "Crypto-Pay-API-Token": self.api_token,
            "Content-Type": "application/json"
        }

    async def _make_request(self, method: str, endpoint: str, **kwargs):
        """Helper to make requests to the Crypto Pay API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(method, f"{self.base_url}{endpoint}", headers=self.headers, **kwargs)
                response.raise_for_status()
                data = response.json()
                if data.get("ok"):
                    return data.get("result")
                else:
                    log.error(f"Crypto Pay API error: {data.get('error')}")
                    return None
        except httpx.HTTPStatusError as e:
            log.error(f"HTTP error calling Crypto Pay API: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            log.error(f"An unexpected error occurred calling Crypto Pay API: {e}")
            return None

    async def get_me(self):
        """Test the API token."""
        return await self._make_request("GET", "getMe")

    async def create_invoice(self, amount: float, currency_type: str = "fiat", fiat: str = "USD", **kwargs):
        """
        Create a new invoice.
        :param amount: The amount of the invoice.
        :param currency_type: Type of the price, 'crypto' or 'fiat'.
        :param fiat: Fiat currency code (e.g., 'USD').
        :param kwargs: Other optional parameters for the createInvoice method.
        """
        payload = {
            "amount": amount,
            "currency_type": currency_type,
            "fiat": fiat,
            **kwargs
        }
        return await self._make_request("POST", "createInvoice", json=payload)

    async def get_balance(self):
        """Get the balance of the app."""
        return await self._make_request("GET", "getBalance")

# Global client instance
cryptopay_client = CryptoPayAPI(
    api_token=config.CRYPTO_PAY_API_TOKEN,
    base_url=config.CRYPTO_PAY_API_BASE_URL
) if config.CRYPTO_PAY_API_TOKEN else None
