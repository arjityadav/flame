"""Agent 1, product analyst: spec and/or exploded view -> structured product profile.

Every later agent works from this profile, so it states facts (parts, functions, electronics, mounting) and says
what it is unsure of rather than guessing regulations.
"""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.llm import ask


class Function(BaseModel):
    name: str = Field(description="e.g. 'stop lamp (braking indication)', 'pedestrian lower-leg impact protection'")
    description: str


class Part(BaseModel):
    name: str
    role: str = Field(description="what the part does in the assembly")
    material: str = Field(description="material if known, else empty")
    electrical: bool = Field(description="part contains or is an electrical/electronic component")


class ProductProfile(BaseModel):
    product_name: str
    product_type: str = Field(description="one line: what kind of vehicle component or system this is")
    vehicle_categories: list[str] = Field(description="UNECE categories, e.g. ['M1']")
    regulatory_scope: str = Field(default="", description="what the specification says about the scope of the "
                                  "regulations or the vehicle categories they cover, verbatim; empty if it "
                                  "says nothing")
    mounting_location: str = Field(description="where on the vehicle, e.g. 'rear, outboard corners'")
    functions: list[Function]
    parts: list[Part]
    electronics: list[str] = Field(description="electrical/electronic content: light sources, PCBs, sensors, radar, "
                                               "cameras, wiring; empty if none")
    vehicle_interfaces: list[str] = Field(description="how it attaches to or interacts with the vehicle and other "
                                                      "systems")
    drawing_observations: list[str] = Field(description="what the exploded view shows that the spec does not say")
    uncertainties: list[str] = Field(description="facts that are assumed or unclear")


SYSTEM = """You are a vehicle homologation engineer. You turn a product specification and/or an exploded-view
drawing of a vehicle component into a precise, factual product profile.

List every distinct function the product performs, separately (a combination lamp with three light functions is
three functions). List the parts. Note all electrical and electronic content, since it matters for which
regulations apply. Note how and where it is mounted on the vehicle. If the drawing shows something the spec does
not mention, record it. When the specification states a fact (a material, a light source technology), it
overrides what you infer from the drawing: do not reopen it as an uncertainty. If the specification says which
vehicle categories the regulations cover beyond the product's own, copy that into regulatory_scope. Do not name
regulations; only describe the product."""


def analyse(case_dir: Path) -> ProductProfile:
    content: list[dict] = []
    for name in ("exploded_view.png", "exploded_view.jpg", "exploded_view.jpeg"):
        img = case_dir / name
        if img.is_file():
            media = "image/png" if img.suffix == ".png" else "image/jpeg"
            content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                         "data": base64.standard_b64encode(img.read_bytes()).decode()}})
            content.append({"type": "text", "text": "Above: the exploded view of the product."})
            break
    spec = case_dir / "spec.md"
    if spec.is_file():
        content.append({"type": "text", "text": f"<specification>\n{spec.read_text()}\n</specification>"})
    if not content:
        raise SystemExit(f"{case_dir} has neither spec.md nor an exploded view")
    content.append({"type": "text", "text": "Write the product profile."})
    return ask(ProductProfile, SYSTEM, content, effort="high")
