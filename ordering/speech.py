"""Spoken forms for numbers. Flash v2.5 does not normalize these itself."""

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """0-999 in words. Above that, fall back to digits."""
    if n < 20:
        return ONES[n]
    if n < 100:
        tens, rest = divmod(n, 10)
        return TENS[tens] + (f" {ONES[rest]}" if rest else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return ONES[hundreds] + " hundred" + (f" {number_words(rest)}" if rest else "")
    return str(n)


def spoken_price(amount: float) -> str:
    """17.00 -> 'seventeen dollars'; 12.50 -> 'twelve dollars and fifty cents'."""
    cents_total = round(amount * 100)
    dollars, cents = divmod(cents_total, 100)

    if dollars == 0 and cents == 0:
        return "no extra charge"

    if dollars == 0:
        return f"{number_words(cents)} cents"

    part = f"{number_words(dollars)} dollar" + ("" if dollars == 1 else "s")
    if cents:
        part += f" and {number_words(cents)} cents"
    return part
