# Copyright (c) Microsoft. All rights reserved.
import functools
from collections.abc import Callable
from typing import Any
from typing import Literal

from ms.common.models.base import FrozenBaseModel

type AnyDict = dict[str, Any]


def dict_adapter[T: FrozenBaseModel](
    input_model_cls: type[T],
    model_dump_mode: Literal["python", "json"] = "python",
    model_dump_by_alias: bool = True,
) -> Callable[
    [Callable[[T], FrozenBaseModel]],
    Callable[[AnyDict], AnyDict],
]:
    """
    A decorator that converts a dict input to a Pydantic model,
    calls the decorated function with the model, and then
    converts the output model back to a dict. This allows devs
    to write strongly-typed function and method signatures when
    integrating with 3rd party providers or libraries that expect
    untyped dicts.

    Example:

    ```python
    class InputModel(FrozenBaseModel):
        x: int
        y: int

    class OutputModel(FrozenBaseModel):
        sum: int
        product: int

    @dict_adapter(InputModel)
    def some_function(input_model: InputModel) -> OutputModel:
        return OutputModel(
            sum=input_model.x + input_model.y,
            product=input_model.x * input_model.y,
        )

    assert some_function({"x": 3, "y": 4}) == {"sum": 7, "product": 12}
    ```
    """

    def decorator(
        func: Callable[[T], FrozenBaseModel],
    ) -> Callable[[AnyDict], AnyDict]:
        @functools.wraps(func)
        def function_wrapper(untyped_data: AnyDict) -> AnyDict:
            input_model = input_model_cls.model_validate(untyped_data)
            output_model = func(input_model)
            return output_model.model_dump(
                mode=model_dump_mode,
                by_alias=model_dump_by_alias,
            )

        return function_wrapper

    return decorator


def dict_adapter_method[T: FrozenBaseModel](
    input_model_cls: type[T],
    model_dump_mode: Literal["python", "json"] = "python",
    model_dump_by_alias: bool = True,
) -> Callable[
    [Callable[[Any, T], FrozenBaseModel]],
    Callable[[Any, AnyDict], AnyDict],
]:
    """
    Same as above but for class methods.

    Example:

    ```python
    class InputModel(FrozenBaseModel):
        a: str
        b: str

    class OutputModel(FrozenBaseModel):
        concatenated: str

    class MyClass:
        @dict_adapter_method(InputModel)
        def some_method(self, input_model: InputModel) -> OutputModel:
            return OutputModel(concatenated=input_model.a + input_model.b)

    my_instance = MyClass()
    assert my_instance.some_method({"a": "Hello, ", "b": "World!"}) == {"concatenated": "Hello, World!"}
    ```
    """

    def decorator(
        func: Callable[[Any, T], FrozenBaseModel],
    ) -> Callable[[Any, AnyDict], AnyDict]:
        @functools.wraps(func)
        def method_wrapper(self_or_cls, untyped_data: AnyDict) -> AnyDict:
            input_model = input_model_cls.model_validate(untyped_data)
            output_model = func(self_or_cls, input_model)
            return output_model.model_dump(
                mode=model_dump_mode,
                by_alias=model_dump_by_alias,
            )

        return method_wrapper

    return decorator
