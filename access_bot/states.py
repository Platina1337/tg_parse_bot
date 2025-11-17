"""States for conversation handlers."""

from enum import Enum

class PaymentStates(Enum):
    """States for payment conversation."""
    WAITING_FOR_RECEIPT = "waiting_for_receipt"
    WAITING_FOR_CRYPTO_CONFIRMATION = "waiting_for_crypto_confirmation"
    PAYMENT_PROCESSING = "payment_processing"

class SubscriptionType(Enum):
    """Types of subscriptions."""
    TRIAL = "trial"
    MONTHLY = "monthly"
    PERMANENT = "permanent"

class PaymentMethod(Enum):
    """Payment methods."""
    STARS = "stars"
    CARD = "card"
    CRYPTO = "crypto"

class CryptoCurrency(Enum):
    """Supported cryptocurrencies."""
    USDT = "USDT"
    BTC = "BTC"
    ETH = "ETH"
