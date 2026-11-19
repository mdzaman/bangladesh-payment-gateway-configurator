"""bdpay - one Python integration for Bangladeshi payment gateways.

Not affiliated with bKash, Nagad, SSLCOMMERZ or any other provider. Merchant-side
integration software only: money always moves through the licensed provider.
"""
from .config import Settings
from .core import BdPayError, Order, Status, fmt_amount, to_poisha
from .service import BdPay

__all__ = ["BdPay", "BdPayError", "Order", "Settings", "Status", "fmt_amount", "to_poisha"]
__version__ = "1.0.0"
