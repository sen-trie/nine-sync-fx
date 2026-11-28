"""Explicit execution context for a renderer-owned scene pass."""
from dataclasses import dataclass, field

from ...fx.errors import FxUnsupported


@dataclass
class PassContext:
    scene: object
    owner: object = None
    images: dict = field(default_factory=dict)
    screen: bool = False

    def validate(self, scene):
        if scene != self.scene:
            raise FxUnsupported("The render-pass context belongs to another scene.")
        if self.owner is not None and scene.objects.get(self.owner.name) != self.owner:
            raise FxUnsupported("The offscreen owner is missing from the pass scene.")
        for name, image in self.images.items():
            if image is None or image.colorspace_settings.name != "Non-Color":
                raise FxUnsupported(f"Scene target {name!r} needs an encoded Non-Color image.")

    @property
    def signature(self):
        render = self.scene.render
        viewport = (render.resolution_x, render.resolution_y, render.resolution_percentage) if self.screen else None
        return self.owner, self.screen, viewport, tuple(sorted(self.images.items()))
