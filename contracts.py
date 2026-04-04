class Observation:
    def __init__(self, ok, summary, changed=False, details=""):
        self.ok = ok
        self.summary = summary
        self.changed = changed
        self.details = details
