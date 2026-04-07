#!/usr/bin/env python3
"""GyroMorpho v2 - Entry point."""
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
