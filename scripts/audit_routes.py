"""Print the effective FastAPI route dependency map."""

from __future__ import annotations

from fastapi.routing import APIRoute

from src.main import app


def dependency_name(call) -> str:
    if call is None:
        return "-"

    return getattr(
        call,
        "__name__",
        call.__class__.__name__,
    )


def collect_dependencies(
    dependant,
) -> set[str]:
    names: set[str] = set()

    for dependency in (
        dependant.dependencies
    ):
        names.add(
            dependency_name(
                dependency.call
            )
        )

        names.update(
            collect_dependencies(
                dependency
            )
        )

    return names


def main() -> None:
    rows = []

    for route in app.routes:
        if not isinstance(
            route,
            APIRoute,
        ):
            continue

        methods = ",".join(
            sorted(
                route.methods or []
            )
        )

        dependencies = sorted(
            collect_dependencies(
                route.dependant
            )
        )

        rows.append(
            (
                route.path,
                methods,
                ",".join(
                    dependencies
                )
                or "-",
            )
        )

    for (
        path,
        methods,
        dependencies,
    ) in sorted(rows):
        print(
            f"{methods:<18} "
            f"{path:<48} "
            f"{dependencies}"
        )


if __name__ == "__main__":
    main()