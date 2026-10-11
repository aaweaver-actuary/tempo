"""Keep invalid preference requests serializable as ordinary HTTP 422 errors."""
import math

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class LearningPriorityRoute(APIRoute):
    def get_route_handler(self):
        handle_request = super().get_route_handler()

        async def handle_learning_priority_request(request):
            try:
                return await handle_request(request)
            except RequestValidationError as error:
                # JSON decoders also accept overflow/nonstandard nonfinite numbers.
                # Echoing those inputs in FastAPI's default error response raises 500.
                details = jsonable_encoder(error.errors(), custom_encoder={
                    float: lambda value: value if math.isfinite(value) else str(value),
                })
                return JSONResponse(status_code=422, content={"detail": details})

        return handle_learning_priority_request
