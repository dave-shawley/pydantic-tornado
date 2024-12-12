class Marker:
    pass


class Body(Marker):
    def __init__(self, description: str | None = None) -> None:
        self.description = description
