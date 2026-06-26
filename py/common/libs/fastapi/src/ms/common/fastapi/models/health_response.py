# Copyright (c) Microsoft. All rights reserved.
from datetime import datetime

from ms.common.fastapi.models.api_response import ApiResponseBaseModel


class HealthResponse(ApiResponseBaseModel):
    status: str
    timestamp: datetime
    build_id: str | None
