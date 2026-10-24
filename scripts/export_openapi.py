"""Export OpenAPI documents into docs/.

    python scripts/export_openapi.py

* docs/fixmytech-api.openapi.json        - FixMyTech backend API (implemented)
* docs/flipvalue-contract.openapi.json   - the PROPOSED FlipValue contract, generated from the same Pydantic
                                           schemas FixMyTech's client validates against
"""

import json
from pathlib import Path

from fixmytech.integrations.flipvalue.mock_server import create_mock_app
from fixmytech.server.app import create_app
from fixmytech.server.config import Settings

DOCS = Path(__file__).resolve().parents[1] / "docs"


def main() -> None:
    app = create_app(Settings(FIXMYTECH_DATABASE_URL="sqlite:///:memory:", FIXMYTECH_ENV="test"), serve_frontend=False)
    (DOCS / "fixmytech-api.openapi.json").write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")

    spec = create_mock_app(api_key="unused").openapi()
    spec["info"] = {
        "title": "FlipValue valuation API - PROPOSED contract v1 (pending partner agreement)",
        "version": "1.0-proposed",
        "description": "Generated from FixMyTech's FlipValue request/response schemas. This is NOT an assertion that "
                       "FlipValue implements it. See INTEGRATION_CONTRACT.md.",
    }
    post = spec["paths"]["/v1/valuations"]["post"]
    post["parameters"] = [p for p in post.get("parameters", []) if p["name"].lower() != "x-api-key"]
    spec["paths"] = {"/v1/valuations": spec["paths"]["/v1/valuations"]}
    spec.setdefault("components", {})["securitySchemes"] = {
        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    spec["security"] = [{"ApiKey": []}]
    (DOCS / "flipvalue-contract.openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"wrote {len(app.openapi()['paths'])} FixMyTech paths and the FlipValue contract to {DOCS}")


if __name__ == "__main__":
    main()
