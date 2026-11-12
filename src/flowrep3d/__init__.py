"""Independent FlowRep reproduction and Atlas-compatible 3D-to-SVG rendering."""
from .types import (CancelledError, CurveNetwork, Drawing, FlowRepError, FlowRepOptions,
                    PolygonMesh, RenderOptions)
from .mesh import from_arrays, load_mesh, prepare_mesh
from .extract import extract_network
from .io import load_network, save_network
from .render import Camera, render_network
from .svg import write_svg

__version__="0.1.2"
__all__=["Camera","CancelledError","CurveNetwork","Drawing","FlowRepError","FlowRepOptions",
         "PolygonMesh","RenderOptions","extract_network","from_arrays","load_mesh","prepare_mesh",
         "load_network","save_network","render_network","write_svg"]

