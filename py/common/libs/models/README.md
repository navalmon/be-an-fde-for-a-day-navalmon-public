# Pydantic Models Library

A common library providing reusable Pydantic model base classes with consistent configuration for the FDE project.

## Overview

This library provides `FrozenBaseModel`, a Pydantic `BaseModel` with frozen configuration that ensures all model instances are immutable after creation. This prevents accidental modifications and ensures data integrity throughout the application lifecycle.

## Installation

This library is part of the FDE uv workspace. To use it in your project within the workspace, add it as a dependency to your `pyproject.toml`:

```toml
[project]
dependencies = [
    "ms-common-models",
    # ... other dependencies
]
```

Then run `uv sync` to install the dependencies.

## Usage

### `FrozenBaseModel`

```python
from ms.common.models.base import FrozenBaseModel

class User(FrozenBaseModel):
    name: str
    age: int
    email: str | None = None

# Create a user instance
user = User(name="Alice", age=30, email="alice@example.com")

# This works fine
print(user.name)  # "Alice"
print(user.age)   # 30

# This will raise a ValidationError because the model is frozen
try:
    user.name = "Bob"  # ❌ ValidationError: Instance is frozen
except ValidationError as e:
    print(f"Error: {e}")
```

### `model_config` Inheritance

If a child class inheriting from `FrozenBaseModel` also specifies a `model_config`, the config from the child class will be _merged_ with the parent's:

```py
from pydantic import BaseModel
from pydantic import ConfigDict


class FrozenBaseModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Child(FrozenBaseModel):
    model_config = ConfigDict(str_to_lower=True)

    x: str


child = Child(x='FOO')
print(child.model_dump())
#> {'x': 'foo'}
print(child.model_config)
#> {'frozen': True, 'str_to_lower': True}
```

## `dict_adapter` Decorator

Use the `dict_adapter` decorator to automatically convert dictionary inputs into Pydantic model instances.
This allows us to write strongly-typed functions when integrating with 3rd party libraries or frameworks
that expect dictionaries (e.g., Azure durable function bindings, Pulumi dynamic providers, etc.).

```python
from ms.common.models.base import FrozenBaseModel
from ms.common.models.dict_adapter import dict_adapter

class InputModel(FrozenBaseModel):
    x: float
    y: float

class OutputModel(FrozenBaseModel):
    sum: float
    product: float

@dict_adapter
def compute(input_model: InputModel) -> OutputModel:
    return OutputModel(
        sum=input_model.x + input_model.y,
        product=input_model.x * input_model.y
    )

assert compute({"x": 3.0, "y": 4.0}) == {"sum": 7.0, "product": 12.0}
```
