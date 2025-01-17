import functools
import subprocess
from abc import abstractmethod

from typing import Optional, Union
import yaml
import torch
import numpy as np
from omegaconf import DictConfig, MISSING


def get_git_hash() -> Optional[str]:
    """
    Safely retrieves the hash of the last commit

    If any failure, returns None
    """
    try:
        process = subprocess.Popen(['git', 'rev-parse', '--short', 'HEAD'], stdout=subprocess.PIPE)
        stdout = process.communicate()[0]
        # Remove the last '\n'
        return stdout.decode(encoding="utf-8")[:-1]
    except ...:
        return None


def assert_debug(condition: bool, message: str = ""):
    """
    Debug Friendly assertion

    Allows to put a breakpoint, and catch any assertion error in debug
    """
    if not condition:
        print(f"[ERROR][ASSERTION]{message}")
        raise AssertionError(message)


def sizes_match(tensor, sizes: list) -> bool:
    """
    Returns True if the sizes matches the tensor shape
    """
    tensor_shape = list(tensor.shape)
    if len(tensor_shape) != len(sizes):
        return False
    for i in range(len(sizes)):
        if sizes[i] != -1 and sizes[i] != tensor_shape[i]:
            return False
    return True


def check_sizes(tensor: (torch.Tensor, np.ndarray), sizes: list):
    """
    Checks the size of a tensor along all its dimensions, against a list of sizes

    The tensor must have the same number of dimensions as the list sizes
    For each dimension, the tensor must have the same size as the corresponding entry in the list
    A size of -1 in the list matches all sizes

    Any Failure raises an AssertionError

    >>> check_sizes(torch.randn(10, 3, 4), [10, 3, 4])
    >>> check_sizes(torch.randn(10, 3, 4), [-1, 3, 4])
    >>> check_sizes(np.random.randn(2, 3, 4), [2, 3, 4])
    >>> #torch__check_sizes(torch.randn(10, 3, 4), [9, 3, 4]) # --> throws an AssertionError
    """
    assert_debug(sizes_match(tensor, sizes),
                 f"[BAD TENSOR SHAPE] Wrong tensor shape got {tensor.shape} expected {sizes}")


def _decorator(d):
    def _d(fn):
        return functools.update_wrapper(d(fn), fn)

    functools.update_wrapper(_d, d)
    return _d


def check_input_size(shape: list):
    """
    A Decorator for batched numpy unary operator
    Which checks the size of array against desired shapes
    """

    @_decorator
    def __decorator(func):
        def _wrapper(array, **kwargs):
            check_sizes(array, shape)
            return func(array, **kwargs)

        return _wrapper

    return __decorator


def batched(*shapes, torch_compatible: bool = True, unwrap_output_tensors: bool = True):
    """
    A Decorator for batched numpy or pytorch operator
    Which extends arrays in the first dimension to match the desired input shapes
    """
    _shapes = []
    for arg in shapes:
        assert_debug(isinstance(arg, list))
        _shapes.append(arg)

    def __unwrap(result):
        if isinstance(result, tuple) or isinstance(result, list):
            _type = type(result)
            return _type([__unwrap(item) for item in result])
        if isinstance(result, np.ndarray) or isinstance(result, torch.Tensor):
            return result[0]
        return result

    def __wrap(tensor):
        if isinstance(tensor, np.ndarray) or isinstance(tensor, torch.Tensor):
            return tensor.reshape(1, *tensor.shape)
        return tensor

    @_decorator
    def __decorator(func):
        def _wrapper(*args, **kwargs):
            extended = None
            batched_args = [*args]
            assert_debug(len(args) >= len(_shapes),
                         "Not enough unnamed arguments, be careful not to pass tensors as named arguments")
            for idx, shape in enumerate(_shapes):
                tensor = args[idx]
                if torch_compatible:
                    assert_debug(isinstance(args[0], np.ndarray) or isinstance(args[0], torch.Tensor))
                else:
                    assert_debug(isinstance(args[0], np.ndarray))

                if extended is None:
                    if len(tensor.shape) == len(shape) - 1:
                        extended = True
                    else:
                        extended = False
                if extended:
                    tensor = __wrap(tensor)
                    batched_args[idx] = tensor
                check_sizes(tensor, shape)

            result = func(*batched_args, **kwargs)
            if extended and unwrap_output_tensors:
                result = __unwrap(result)
            return result

        return _wrapper

    return __decorator


def get_config(config_file: str):
    try:
        with open(config_file, "r") as file:
            model_params: dict = yaml.safe_load(file)
            return model_params
    except (FileNotFoundError, IOError):
        raise IOError(f"Could not open the yml file {config_file}")


# ----------------------------------------------------------------------------------------------------------------------
def remove_nan(tensor: Union[torch.Tensor, np.ndarray]):
    """Removes all `nan` values from a one or two dimensional tensor"""
    ndims = len(tensor.shape)
    assert_debug(ndims <= 2)

    if isinstance(tensor, torch.Tensor):
        _filter = torch.isnan(tensor)
        if ndims == 2:
            _filter = ~torch.any(_filter, dim=1)
    elif isinstance(tensor, np.ndarray):
        _filter = np.isnan(tensor)
        if ndims == 2:
            _filter = ~np.any(_filter, axis=1)
    else:
        raise NotImplementedError("The tensor shape does not exist")

    return tensor[_filter], _filter


def modify_nan_pmap(tensor: torch.Tensor, default_value: float = 0.0):
    """Set all pixel data of a projection map which have a nan to a default value"""
    check_sizes(tensor, [-1, -1, -1, -1])
    _filter: torch.Tensor = torch.any(torch.isnan(tensor), dim=1, keepdim=True)
    _filter = _filter.repeat(1, tensor.shape[1], 1, 1)
    new_tensor = tensor.clone()
    new_tensor[_filter] = default_value

    return new_tensor


# ----------------------------------------------------------------------------------------------------------------------
class ObjectLoaderEnum:
    """
    ObjectLoaderEnum is a utility class to load object defined from hydra's structured config
    """

    @classmethod
    def load(cls, config: DictConfig, **kwargs):
        if isinstance(config, DictConfig):
            assert_debug(cls.type_name() in config, f"The config does not contains the key : '{cls.type_name()}'")
            _type = config.get(cls.type_name())
        else:
            assert_debug(hasattr(config, cls.type_name()), f"The object {config} is not a valid config.")
            _type = getattr(config, cls.type_name())
        assert_debug(hasattr(cls, "__members__"))
        assert_debug(_type in cls.__members__,
                     f"Unknown type `{_type}`. Existing members are : {cls.__members__.keys()}")

        _class, _config = cls.__members__[_type].value
        if _class is None or _config is None:
            return None

        if isinstance(config, DictConfig):
            # Replace the DictConfig by an instance of the Dataclass
            # Do not yet raise error for MISSING data (letting the possibility to complete at runtime defaults)
            new_config = _config()
            for key in config:
                value = config.get(key, MISSING)
                if value != MISSING:
                    setattr(new_config, key, value)
            config = new_config

        return _class(config, **kwargs)

    @classmethod
    def type_name(cls):
        raise NotImplementedError("")
