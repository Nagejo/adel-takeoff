"""Run the takeoff app: python -m adel_takeoff"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run(
        "adel_takeoff.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
