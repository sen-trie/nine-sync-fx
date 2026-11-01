import os

V = [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
     (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]
F = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]
UV = [(0, 1), (1, 1), (1, 0), (0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]


def build():
    out = ["xof 0303txt 0032", "", "Mesh {", " %d;" % len(V)]
    out += [" %.6f;%.6f;%.6f;%s" % (*v, "," if i < len(V) - 1 else ";") for i, v in enumerate(V)]
    out.append(" %d;" % len(F))
    out += [" 4;%d,%d,%d,%d;%s" % (*f, "," if i < len(F) - 1 else ";") for i, f in enumerate(F)]
    out += [
        " MeshMaterialList {", "  1;", "  %d;" % len(F),
        "  " + ",".join("0" for _ in F) + ";;",
        "  Material {", "   0.800000;0.800000;0.800000;1.000000;;", "   10.000000;",
        "   0.000000;0.000000;0.000000;;", "   0.000000;0.000000;0.000000;;", "  }", " }",
        " MeshTextureCoords {", " %d;" % len(UV),
    ]
    out += ["  %.6f;%.6f;%s" % (*uv, "," if i < len(UV) - 1 else ";") for i, uv in enumerate(UV)]
    out += [" }", "}", ""]
    return "\n".join(out)


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "plain", "plainthing.x")
    with open(target, "w", newline="\n") as handle:
        handle.write(build())
    print(target)
