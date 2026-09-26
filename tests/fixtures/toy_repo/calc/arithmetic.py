def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def average(numbers):
    # BUG: divides by len(numbers) + 1, off-by-one
    return sum(numbers) / (len(numbers) + 1)
