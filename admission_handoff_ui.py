"""One dialog interaction cannot submit a second committed handoff."""

from dataclasses import dataclass


@dataclass
class HandoffSubmission:
    active: bool = False
    committed: bool = False

    @property
    def blocked(self):
        return self.active or self.committed

    def begin(self):
        if self.blocked:
            return False
        self.active = True
        return True

    def mark_committed(self):
        self.committed = True

    def finish(self):
        self.active = False
        return not self.committed
