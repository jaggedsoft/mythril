from ethereum import utils
from mythril.laser.smt import (
    BitVec,
    Function,
    URem,
    symbol_factory,
    ULE,
    And,
    ULT,
    Bool,
    Or,
    simplify
)
from typing import Dict, Tuple, List, Optional

TOTAL_PARTS = 10 ** 40
PART = (2 ** 256 - 1) // TOTAL_PARTS
INTERVAL_DIFFERENCE = 10 ** 30
hash_matcher = "fffffff"  # This is usually the prefix for the hash in the output


class KeccakFunctionManager:
    """
    A bunch of uninterpreted functions are considered like keccak256_160 ,...
    where keccak256_160 means the input of keccak256() is 160 bit number.
    the range of these functions are constrained to some mutually disjoint intervals
    All the hashes modulo 64 are 0 as we need a spread among hashes for array type data structures
    All the functions are kind of one to one due to constraint of the existence of inverse
    for each encountered input.
    For more info https://files.sri.inf.ethz.ch/website/papers/sp20-verx.pdf
    """

    def __init__(self):
        self.store_function = {}  # type: Dict[int, Tuple[Function, Function]]
        self.interval_hook_for_size = {}  # type: Dict[int, int]
        self._index_counter = TOTAL_PARTS - 34534
        self.hash_result_store = {}  # type: Dict[int, List[BitVec]]
        self.quick_inverse = {}  # type: Dict[BitVec, BitVec]  # This is for VMTests
        self.concrete_hashes = {}  # type: Dict[BitVec, BitVec]

    @staticmethod
    def find_concrete_keccak(data: BitVec) -> BitVec:
        """
        Calculates concrete keccak
        :param data: input bitvecval
        :return: concrete keccak output
        """
        keccak = symbol_factory.BitVecVal(
            int.from_bytes(
                utils.sha3(data.value.to_bytes(data.size() // 8, byteorder="big")),
                "big",
            ),
            256,
        )
        return keccak

    def get_function(self, length: int) -> Tuple[Function, Function]:
        """
        Returns the keccak functions for the corresponding length
        :param length: input size
        :return: tuple of keccak and it's inverse
        """
        try:
            func, inverse = self.store_function[length]
        except KeyError:
            func = Function("keccak256_{}".format(length), length, 256)
            inverse = Function("keccak256_{}-1".format(length), 256, length)
            self.store_function[length] = (func, inverse)
            self.hash_result_store[length] = []
        return func, inverse

    @staticmethod
    def get_empty_keccak_hash() -> BitVec:
        """
        returns sha3("")
        :return:
        """
        val = 89477152217924674838424037953991966239322087453347756267410168184682657981552
        return symbol_factory.BitVecVal(val, 256)

    def create_keccak(self, data: BitVec) -> Tuple[BitVec, Bool]:
        """
        Creates Keccak of the data
        :param data: input
        :return: Tuple of keccak and the condition it should satisfy
        """
        length = data.size()
        data.simplify()
        func, inverse = self.get_function(length)
        self.quick_inverse[func(data)] = data
        if data.symbolic is False:
            concrete_hash = simplify(self.find_concrete_keccak(data))
            self.concrete_hashes[data] = concrete_hash
            self.quick_inverse[concrete_hash] = data
            # This condition is essential to avoid some edge cases
            condition = And(func(data) == concrete_hash, inverse(func(data)) == data)
            return concrete_hash, condition

        condition = self._create_condition(func_input=data)
        self.hash_result_store[length].append(func(data))
        return func(data), condition

    def get_concrete_hash_data(self, model) -> Dict[int, List[Optional[int]]]:
        """
        returns concrete values of hashes in the self.hash_result_store
        :param model: The z3 model to query for concrete values
        :return: A dictionary with concrete hashes { <hash_input_size> : [<concrete_hash>, <concrete_hash>]}
        """
        concrete_hashes = {}  # type: Dict[int, List[Optional[int]]]
        for size in self.hash_result_store:
            concrete_hashes[size] = []
            for val in self.hash_result_store[size]:
                eval_ = model.eval(val.raw)
                try:
                    concrete_val = eval_.as_long()
                    concrete_hashes[size].append(concrete_val)
                except AttributeError:
                    continue
        return concrete_hashes

    def _create_condition(self, func_input: BitVec) -> Bool:
        """
        Creates the constraints for hash
        :param func_input: input of the hash
        :return: condition
        """
        length = func_input.size()
        func, inv = self.get_function(length)
        try:
            index = self.interval_hook_for_size[length]
        except KeyError:
            self.interval_hook_for_size[length] = self._index_counter
            index = self._index_counter
            self._index_counter -= INTERVAL_DIFFERENCE

        lower_bound = index * PART
        upper_bound = lower_bound + PART

        cond = And(
            inv(func(func_input)) == func_input,
            ULE(symbol_factory.BitVecVal(lower_bound, 256), func(func_input)),
            ULT(func(func_input), symbol_factory.BitVecVal(upper_bound, 256)),
            URem(func(func_input), symbol_factory.BitVecVal(64, 256)) == 0,
        )
        concrete_cond = symbol_factory.Bool(False)
        for key, keccak in self.concrete_hashes.items():
            hash_eq = And(func(func_input) == keccak, key == func_input)
            concrete_cond = Or(concrete_cond, hash_eq)
        return And(inv(func(func_input)) == func_input, Or(cond, concrete_cond))


keccak_function_manager = KeccakFunctionManager()
