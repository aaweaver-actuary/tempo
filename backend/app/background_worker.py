"""Process entry point; lifecycle and concrete choices belong to composition."""
import asyncio
import logging
from .product_composition.interfaces.worker import run_legacy_worker

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_legacy_worker())
