import argparse
import json
import sys

from .package import scan_package


def main():
    parser = argparse.ArgumentParser(description="Inspect an unpacked MME package and print a JSON capability report.")
    parser.add_argument("directory")
    parser.add_argument("--source", help="Creator URL or local provenance identifier")
    parser.add_argument("--probe", action="store_true", help="Evaluate local accessory shaders on one synthetic sample")
    args = parser.parse_args()
    try:
        report = scan_package(args.directory, args.source, probe=args.probe)
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")
    json.dump(report, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
