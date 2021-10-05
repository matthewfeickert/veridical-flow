'''Set of modules to be parallelized over in a pipeline.
Function arguments are each a list
'''
PREV_KEY = '__prev__'

import numpy as np
import ray

from vflow.convert import *
from vflow.vfunc import Vfunc, AsyncModule
from vflow.smart_subkey import SmartSubkey


class Vset:
    def __init__(self, name: str, modules, module_keys: list = None,
                 is_async: bool = False, output_matching: bool = False):
        '''
        todo: include prev and next and change functions to include that.
        Params
        -------
        name: str
            name of this moduleset
        modules: list or dict
            dictionary of functions that we want to associate with
        module_keys: list (optional)
            list of names corresponding to each module
        is_async: bool (optional)
            if True, modules are computed asynchronously
        '''
        self.name = name
        self._fitted = False
        self.out = None  # outputs
        self._async = is_async
        self._output_matching = output_matching
        # check if any of the modules are AsyncModules
        # if so, we'll make then all AsyncModules later on
        if not self._async and np.any([isinstance(mod, AsyncModule) for mod in modules]):
            self._async = True
        if type(modules) is dict:
            self.modules = modules
        elif type(modules) is list:
            if module_keys is not None:
                assert type(module_keys) is list, 'modules passed as list but module_names is not a list'
                assert len(modules) == len(
                    module_keys), 'modules list and module_names list do not have the same length'
                # TODO: add more checking of module_keys
                module_keys = [self.__create_smart_subkey(k) if isinstance(k, tuple) else
                                (self.__create_smart_subkey(k), ) for k in module_keys]
            else:
                module_keys = [(self.__create_smart_subkey(f'{name}_{i}'), ) for i in range(len(modules))]
            # convert module keys to singleton tuples
            self.modules = dict(zip(module_keys, modules))
        # if needed, wrap the modules in the Vfunc or AsyncModule class
        for k, v in self.modules.items():
            if self._async:
                if not isinstance(v, AsyncModule):
                    self.modules[k] = AsyncModule(k[0], v)
            elif not isinstance(v, Vfunc):
                self.modules[k] = Vfunc(k[0], v)

    def apply_func(self, *args, out_dict=None, matching='cartesian', order='typical', **kwargs):
        '''
        Params
        ------
        *args: List[Dict]: takes multiple dicts and combines them into one.
                Then runs modules on each item in combined dict.
        out_dict: the dictionary to pass to the matching function. If None, defaults to self.modules.

        Returns
        -------
        results: dict
            with items being determined by functions in module set.
            Functions and input dictionaries are currently matched using  matching = 'cartesian' format.
                e.g. inputs:    module = {LR : logistic}, data = {train_1 : [X1,y1], train2 : [X2,y2]}
                     out:    out_dict = {(train_1, LR)  : fitted logistic, (train_2, LR) :  fitted logistic}.
            Currently matching = 'subset' is not used...
        '''
        if out_dict is None:
            out_dict = self.modules

        # deepcopy args to avoid mutating them
        args = deepcopy(args)

        for ele in args:
            if not isinstance(ele, dict):
                raise Exception('Need to run init_args before calling module_set!')
            if self._async:
                # send data to the remote object store
                for k, v in ele.items():
                    if k != PREV_KEY:
                        ele[k] = ray.put(v)

        data_dict = combine_dicts(*args)
        out_dict = apply_modules(out_dict, data_dict)

        if self._async:
            out_keys = list(out_dict.keys())
            out_vals = ray.get(list(out_dict.values()))
            out_dict = dict(zip(out_keys, out_vals))

        self.__prev__ = data_dict[PREV_KEY]
        out_dict[PREV_KEY] = (self,)

        if self._output_matching:
            # the final subkey of keys in out_dict should be key created during
            # Vset.__init__()
            out_keys = out_dict.keys()
        return out_dict

    def fit(self, *args, **kwargs):
        '''
        '''
        if self._fitted:
            return self
        out_dict = {}
        for k, v in self.modules.items():
            out_dict[k] = v.fit
        self.out = self.apply_func(*args, out_dict=out_dict, **kwargs)
        self._fitted = True
        return self

    def transform(self, *args, **kwargs):
        '''todo: fix this method
        '''
        results = []
        for out in self.output:
            result = out.transform(*args, **kwargs)
            results.append(result)
        return results

    def predict(self, *args, **kwargs):
        if not self._fitted:
            raise AttributeError('Please fit the Vset object before calling the predict method.')
        pred_dict = {}
        for k, v in self.out.items():
            if hasattr(v, 'predict'):
                pred_dict[k] = v.predict
        return self.apply_func(*args, out_dict=pred_dict, matching='cartesian', order='backwards', **kwargs)

    def predict_proba(self, *args, **kwargs):
        if not self._fitted:
            raise AttributeError('Please fit the Vset object before calling the predict_proba method.')
        pred_dict = {}
        for k, v in self.out.items():
            if hasattr(v, 'predict_proba'):
                pred_dict[k] = v.predict_proba
        return self.apply_func(*args, out_dict=pred_dict, matching='cartesian', order='backwards', **kwargs)

    def evaluate(self, *args, **kwargs):
        '''Combines dicts before calling apply_func
        '''
        return self.apply_func(*args, **kwargs)

    def __call__(self, *args, n_out: int = None, **kwargs):
        '''
        '''
        if n_out is None:
            n_out = len(args)
        out = sep_dicts(self.apply_func(*args, **kwargs), n_out=n_out)
        return out

    def __getitem__(self, i):
        '''Accesses ith item in the module set
        '''
        return self.modules[i]

    def __contains__(self, key):
        '''Returns true if modules is a dict and key is one of its keys
        '''
        if isinstance(self.modules, dict):
            return key in self.modules.keys()
        return False

    def keys(self):
        if isinstance(self.modules, dict):
            return self.modules.keys()
        return {}.keys()

    def __len__(self):
        return len(self.modules)

    def __str__(self):
        return 'Vset(' + self.name + ')'

    def __create_smart_subkey(self, subkey):
        return SmartSubkey(subkey, self.name, self._output_matching)