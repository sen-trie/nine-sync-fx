import os


# Texture names in .x files use Windows paths and often point at folders that no longer exist, so try the
# stored path, then the file's own folder, then its textures/ folder.
def resolve_x_texture(name, folder):
    cleaned = name.replace("\\\\", "\\").replace("\\", "/")
    base = cleaned.rsplit("/", 1)[-1]
    for candidate in (
        cleaned,
        os.path.join(folder, base),
        os.path.join(folder, "textures", base),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None
