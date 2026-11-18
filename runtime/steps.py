# Generators are how a long job reports progress and can be stopped between units: each step yields (fraction, text) and the
# return value is the job's result.
def run_steps(steps):
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


# Re-yields another generator's steps with its 0..1 fraction mapped into [low, high]; closing this one closes the inner one.
def scaled(steps, low, high):
    try:
        while True:
            try:
                fraction, text = next(steps)
            except StopIteration as done:
                return done.value
            yield low + (high - low) * fraction, text
    finally:
        steps.close()
