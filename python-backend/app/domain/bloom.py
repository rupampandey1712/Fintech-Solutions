"""Bloom filter: a compact set that answers "definitely not present" or "probably present".

Used to refuse common passwords at registration without keeping the password list in a set:
a filter for a million passwords at a 0.1% false-positive rate needs about 1.8 MB instead of
tens of megabytes. A false positive only asks a user to pick a different password, which is
the safe direction; there are no false negatives.

Bit positions use double hashing over SHA-256 (Kirsch-Mitzenmacher): two 64-bit values h1, h2
from the digest give position i as (h1 + i * h2) mod m. The modulo is applied to each term
before combining so the arithmetic fits in 64 bits, which lets the .NET implementation
(FinCore.Domain.BloomFilter) compute exactly the same positions.
"""

import hashlib
import math
from collections.abc import Iterable, Iterator


class BloomFilter:
    def __init__(self, bit_count: int, hash_count: int) -> None:
        if bit_count < 1 or hash_count < 1:
            raise ValueError("a Bloom filter needs at least one bit and one hash")
        self.bit_count = bit_count
        self.hash_count = hash_count
        self._bits = bytearray((bit_count + 7) // 8)

    @classmethod
    def for_capacity(cls, expected_items: int, false_positive_rate: float) -> "BloomFilter":
        """Size the filter for `expected_items` at the given false-positive rate.

        m = -n ln(p) / (ln 2)^2 bits and k = (m / n) ln 2 hashes are the optimal choices.
        """
        if not 0 < false_positive_rate < 1:
            raise ValueError("false_positive_rate must be between 0 and 1")
        n = max(1, expected_items)
        bits = math.ceil(-n * math.log(false_positive_rate) / math.log(2) ** 2)
        hashes = max(1, round(bits / n * math.log(2)))
        return cls(bits, hashes)

    @classmethod
    def of(cls, items: Iterable[str], false_positive_rate: float) -> "BloomFilter":
        values = list(items)
        bloom = cls.for_capacity(len(values), false_positive_rate)
        for value in values:
            bloom.add(value)
        return bloom

    def positions(self, item: str) -> Iterator[int]:
        digest = hashlib.sha256(item.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:8], "big") % self.bit_count
        # Odd, so the probe sequence does not collapse when h2 shares a factor with m.
        h2 = (int.from_bytes(digest[8:16], "big") | 1) % self.bit_count
        for index in range(self.hash_count):
            yield (h1 + index * h2) % self.bit_count

    def add(self, item: str) -> None:
        for position in self.positions(item):
            self._bits[position >> 3] |= 1 << (position & 7)

    def __contains__(self, item: str) -> bool:
        return all(self._bits[position >> 3] & (1 << (position & 7))
                   for position in self.positions(item))
