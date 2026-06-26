# FastAPI Common Library

This library provides middleware and exception handling utilities for FastAPI applications.

## Exception Handler Middleware

The `ExceptionHandlerMiddleware` automatically logs uncaught exceptions and returns appropriate HTTP responses based on custom error code mappings.

## Error Codes

Each application must define its own error codes using enums. These error codes will be used both when throwing `FdeCustomException` in application code and when configuring the error code mapper.

### Defining Error Codes

```python
# In your app: error_codes.py
from enum import StrEnum

class UserErrorCode(StrEnum):
    NOT_FOUND = "USER_NOT_FOUND"
    INVALID_EMAIL = "USER_INVALID_EMAIL"
    DUPLICATE_EMAIL = "USER_DUPLICATE_EMAIL"

class DocumentErrorCode(StrEnum):
    INVALID_FORMAT = "DOCUMENT_INVALID_FORMAT"
    SIZE_TOO_LARGE = "DOCUMENT_SIZE_TOO_LARGE"
    PROCESSING_FAILED = "DOCUMENT_PROCESSING_FAILED"
```

**Note**: Use domain-prefixed error code values to ensure uniqueness across different enum classes and prevent conflicts in logging and monitoring systems.

## Error Code Mapping

Each application must implement an `ErrorCodeMapper` to define how error codes map to HTTP status codes and client error messages.

### Creating an Error Code Mapper

```python
# In your app: error_mapping.py
from enum import StrEnum
from http import HTTPStatus

from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper

from src.error_codes import DocumentErrorCode
from src.error_codes import UserErrorCode

class AppErrorCodeMapper(ErrorCodeMapper):
    @property
    def mappings(self) -> dict[StrEnum, ErrorCodeMapper.ErrorCodePropertyBag]:
        return {
            # User errors
            UserErrorCode.NOT_FOUND: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.NOT_FOUND,
                client_message="User not found"
            ),
            UserErrorCode.INVALID_EMAIL: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.BAD_REQUEST,
                client_message="Invalid email format"
            ),
            UserErrorCode.DUPLICATE_EMAIL: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.CONFLICT,
                client_message="Email already exists"
            ),

            # Document errors
            DocumentErrorCode.INVALID_FORMAT: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.BAD_REQUEST,
                client_message="Document format not supported"
            ),
            DocumentErrorCode.SIZE_TOO_LARGE: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                client_message="Document size exceeds limit"
            ),
            DocumentErrorCode.PROCESSING_FAILED: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                client_message="Document processing failed"
            ),
        }
```

### Setup

```python
from fastapi import FastAPI

from ms.common.fastapi import create_fastapi_app

from src.error_mapping import AppErrorCodeMapper

app = create_fastapi_app(
    title="My API",
    error_mapper=AppErrorCodeMapper(),
)
```

## Using `FdeCustomException`

Applications use the single `FdeCustomException` class with their defined error codes. The error code determines both the HTTP status code and user-facing client message returned.

### Usage in Application Code

```python
from ms.common.fastapi.exception.fde_custom_exception import FdeCustomException

from src.error_codes import DocumentErrorCode
from src.error_codes import UserErrorCode

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    user = await user_service.get_user(user_id)
    if not user:
        raise FdeCustomException(
            error_code=UserErrorCode.NOT_FOUND,
            message="Database query returned no results",
            data={"user_id": user_id}
        )
    return user

@app.post("/users")
async def create_user(email: str):
    if not is_valid_email(email):
        raise FdeCustomException(
            error_code=UserErrorCode.INVALID_EMAIL,
            message=f"Email format validation failed for: {email}",
            data={"email": email}
        )
    # ... create user logic

@app.post("/documents")
async def upload_document(file_size: int):
    if file_size > MAX_FILE_SIZE:
        raise FdeCustomException(
            error_code=DocumentErrorCode.SIZE_TOO_LARGE,
            message=f"File size {file_size} exceeds limit {MAX_FILE_SIZE}",
            data={"file_size": file_size, "max_size": MAX_FILE_SIZE}
        )
    # ... process document
```

When exceptions are thrown, the middleware will:
1. Look up the HTTP status code and client message using the configured `ErrorCodeMapper`
1. Log the exception with full context (method, path, error details, custom data)
1. For `FdeCustomException`: Returns HTTP response with the mapped status code and client message
1. For all other exceptions: Returns a generic 500 Internal Server Error response. **Note**: the middleware still logs the full exception details for uncaught exceptions; there is no need to wrap all exceptions in `FdeCustomException`.
1. Include structured logging data for Application Insights or other log analysis tools

### Structured Logging Fields

For `FdeCustomException`, the middleware automatically includes these structured logging fields:
- `error_code`: The error code value (e.g., "USER_NOT_FOUND")
- `error_code_class`: The enum class name (e.g., "UserErrorCode")
- `status_code`: The HTTP status code that will be returned
- `client_message`: The user-facing error message
- Plus any additional data from the exception's `data` parameter

## Error Handling Best Practices

### Don't catch exceptions just to log them

Don't catch exceptions just to log them and re-raise. The middleware already logs uncaught exceptions with full context. **Only catch exceptions if you need to handle them specifically**.

#### ❌ Violation

```python
@app.get("/users/{user_id}")
async def get_user(user_id: str):
    try:
        return await user_service.get_user(user_id)
    except Exception as e:
        logger.error(f"Failed to get user {user_id}: {e}")
        raise
```

#### ✅ Fix

```python
@app.get("/users/{user_id}")
async def get_user(user_id: str):
    return await user_service.get_user(user_id)
```

### Don't wrap generic exceptions in `FdeCustomException`

Don't wrap exceptions in `FdeCustomException` unless you need to customize the status code or message. The middleware will log all details of the uncaught error and return a generic `500: Internal Server Error` to the client.

#### ❌ Violation

Unnecessarily wrapping an entire method's logic in a try/except makes code harder to read and obscures the original exception:

```python
class MyService:
    def some_method(self):
        try:
            # Some code that may raise exceptions
            ...
        except Exception as e:
            raise FdeCustomException(
                error_code=SomeErrorCode.GENERIC_FAILURE,
                message="An error occurred in MyService.some_method",
                data={"original_exception": str(e)}
            ) from e
```

#### ✅ Fix

Unless we need to customize the status code or message, or suppress specific exceptions, we can let the middleware handle logging any uncaught exceptions:

```python
class MyService:
    def some_method(self):
        # Some code that may raise exceptions
        ...
```

### Don't throw and immediately catch the same exception

Don't throw and then immediately catch the same exception; this adds unnecessary complexity and makes code hard to read. Just throw the exception once.

#### ❌ Violation

Suppose we have a method that checks if `x` is even, but we want to throw a custom exception if `x` is negative. Wrapping the entire function in try/except makes a simple method very complicated and hard to read:

```python
def is_x_even(x: int) -> bool:
    try:
        if x < 0:
            raise FdeCustomException(
                error_code=MyErrorCode.NEGATIVE_X,
                message="x is negative",
                data={"x": x},
            )

        return x % 2 == 0
    except FdeCustomException:
        raise
    except Exception as e:
        raise FdeCustomException(
            error_code=MyErrorCode.GENERIC_FAILURE,
            message="An error occurred in is_x_even",
            data={"original_exception": str(e)},
        ) from e
```

#### ✅ Fix

Let generic failures be handled by the middleware. Use `FdeCustomException` to capture specific error conditions only:

```python
def is_x_even(x: int) -> bool:
    if x < 0:
        raise FdeCustomException(
            error_code=MyErrorCode.NEGATIVE_X,
            message="x is negative",
            data={"x": x},
        )

    return x % 2 == 0
```

### Don't double log

Don't both log and raise an `FdeCustomException`. The middleware will log the exception with full context, so additional logging is redundant.

#### ❌ Violation

```python
def is_x_even(x: int) -> bool:
    if x < 0:
        logger.error(f"x is negative: {x}")
        raise FdeCustomException(
            error_code=MyErrorCode.NEGATIVE_X,
            message="x is negative",
            data={"x": x},
        )

    return x % 2 == 0
```

#### ✅ Fix

```python
def is_x_even(x: int) -> bool:
    if x < 0:
        raise FdeCustomException(
            error_code=MyErrorCode.NEGATIVE_X,
            message="x is negative",
            data={"x": x},
        )

    return x % 2 == 0
```

## `ApiRequestBaseModel` & `ApiResponseBaseModel`

* All FastAPI **request** models should inherit from `ApiRequestBaseModel`. This class takes care of automatically converting field names from `camelCase` to `snake_case` when deserializing requests from clients.
* All FastAPI **response** models should inherit from `ApiResponseBaseModel`. This class takes care of automatically converting field names from `snake_case` to `camelCase` when serializing responses to clients.

### Basic Usage

```python
from ms.common.fastapi.models.api_request import ApiRequestBaseModel
from ms.common.fastapi.models.api_response import ApiResponseBaseModel


class MyUserRequestModel(ApiRequestBaseModel):
    user_name: str
    email_address: str


class MyUserResponseModel(ApiResponseBaseModel):
    user_id: str
    user_name: str
    email_address: str


@app.post("/users")
async def create_user(user: MyUserRequestModel) -> MyUserResponseModel:
    # Client sends:
    # {
    #   "userName": "john_doe",
    #   "emailAddress": "john@example.com"
    # }
    # Automatically deserialized to snake_case:
    assert user.user_name == "john_doe"
    assert user.email_address == "john@example.com"

    # ... create user logic
    user = await user_service.create_user(user)

    # API returns:
    # {
    #   "userId": "abc123",
    #   "userName": "john_doe",
    #   "emailAddress": "john@example.com"
    # }
    return MyUserResponseModel(
        user_id=user.id,
        user_name=user.name,
        email_address=user.email,
    )
    # Automatically serialized to camelCase by FastAPI
```

### Nested Models

When using nested models in your API request or response models, **all nested models must inherit from the same base class** as the parent model. This ensures that the camelCase/snake_case conversion works correctly at all levels of nesting.

The framework will raise a `TypeError` at class definition time if a nested model does not inherit from the same base class as its parent.

```python
from ms.common.fastapi.models.api_request import ApiRequestBaseModel


class AddressRequest(ApiRequestBaseModel):
    street_name: str
    city_name: str
    zip_code: str


class CreateUserRequest(ApiRequestBaseModel):
    user_name: str
    email_address: str
    home_address: AddressRequest


@app.post("/users")
async def create_user(user: CreateUserRequest) -> UserResponse:
    # Client sends:
    # {
    #   "userName": "john_doe",
    #   "emailAddress": "john@example.com",
    #   "homeAddress": {
    #     "streetName": "123 Main St",
    #     "cityName": "Seattle",
    #     "zipCode": "98101"
    #   }
    # }

    # Automatically deserialized to snake_case:
    assert user.user_name == "john_doe"
    assert user.home_address.street_name == "123 Main St"
    ...
```

### Separation of Concerns: API Models vs Domain Models

API-facing models should be separate from internal domain models. This prevents leaking implementation details to API clients and keeps your API contract clean and stable.

#### ❌ Violation: Using API Models Internally

```python
from ms.common.fastapi.models.api_request import ApiRequestBaseModel
from ms.common.fastapi.models.api_response import ApiResponseBaseModel


class UserResponse(ApiResponseBaseModel):
    user_id: str
    user_name: str
    email_address: str
    # ❌ Internal fields leaked to API clients:
    password_hash: str
    created_timestamp: int
    last_login_timestamp: int
    database_partition_key: str


class UserService:
    async def get_user(self, user_id: str) -> UserResponse:
        # ❌ Using API model for internal logic
        db_user = await self.db.query(user_id)
        return UserResponse(
            user_id=db_user.id,
            user_name=db_user.name,
            email_address=db_user.email,
            password_hash=db_user.password_hash,  # Exposed to client!
            created_timestamp=db_user.created_at,
            last_login_timestamp=db_user.last_login,
            database_partition_key=db_user.partition_key,
        )
```

#### ✅ Fix: Separate Domain and API Models

```python
from ms.common.fastapi.models.api_response import ApiResponseBaseModel
from ms.common.models.base import FrozenBaseModel


# Domain model for internal use
class User(FrozenBaseModel):
    user_id: str
    user_name: str
    email_address: str
    password_hash: str
    created_timestamp: int
    last_login_timestamp: int
    database_partition_key: str


# API model for external clients
class UserResponse(ApiResponseBaseModel):
    user_id: str
    user_name: str
    email_address: str


class UserService:
    async def get_user(self, user_id: str) -> User:
        # Internal logic uses domain model
        db_user = await self.db.query(user_id)
        return User(
            user_id=db_user.id,
            user_name=db_user.name,
            email_address=db_user.email,
            password_hash=db_user.password_hash,
            created_timestamp=db_user.created_at,
            last_login_timestamp=db_user.last_login,
            database_partition_key=db_user.partition_key,
        )


@app.get("/users/{user_id}")
async def get_user(user_id: str) -> UserResponse:
    # Convert domain model to API model at the boundary
    user = await user_service.get_user(user_id)
    return UserResponse(
        user_id=user.user_id,
        user_name=user.user_name,
        email_address=user.email_address,
    )
```

This pattern:
- **Keeps API contracts stable**: Internal changes don't affect the API
- **Prevents data leaks**: Only necessary fields are exposed to clients
- **Enables independent evolution**: Domain models and API models can change independently
- **Improves security**: Sensitive fields or fields irrelevant to the client stay internal
