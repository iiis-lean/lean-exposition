"""Start the local reader server; this is not a separate reading CLI protocol."""
import argparse
import json
from pathlib import Path

from lean_exposition.exposition import ContentStore
from lean_exposition.models import Workspace
from lean_exposition.reading import ReaderService
from lean_exposition.interfaces.server import create_app
from .demo import create_demo


def main():
    parser = argparse.ArgumentParser(description="Run the loopback Lean Exposition reader server")
    parser.add_argument("--state-dir", type=Path, default=Path("data/reader-demo"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static-dir", type=Path, help="Explicit frontend asset directory")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--hierarchy", type=Path)
    parser.add_argument("--content", type=Path)
    parser.add_argument("--features", type=Path, help="FeatureSet JSON matching the single package")
    parser.add_argument("--recommendation-policy", choices=("structural", "random"), default="structural")
    parser.add_argument("--packages", type=Path, help="JSON array of workspace/hierarchy/content paths for a multilingual library")
    parser.add_argument("--runtime-config", type=Path, help="ApiConfig JSON; credentials are referenced only by environment variable name")
    parser.add_argument("--generation-strategy", choices=("sequential", "concurrent"), default="concurrent",
                        help="Sibling writing strategy for uncached API generation")
    args = parser.parse_args()
    generation_executor = None
    feature_paths = {}
    if args.runtime_config:
        from lean_exposition.runtime import ApiConfig, StructuredExecutor
        config = ApiConfig(**json.loads(args.runtime_config.read_text()))
        generation_executor = StructuredExecutor(config)
    if args.packages:
        if any((args.workspace, args.hierarchy, args.content, args.features)):
            parser.error("--packages cannot be combined with the single-package arguments")
        packages = json.loads(args.packages.read_text())
        stores = []
        for package in packages:
            paths = {key: args.packages.parent / package[key] for key in ("workspace", "hierarchy", "content")}
            stores.append(ContentStore(Workspace.from_json(paths["workspace"].read_text()),
                                       json.loads(paths["hierarchy"].read_text()), paths["content"],
                                       executor=generation_executor,
                                       generation_strategy=args.generation_strategy))
            if package.get("features"):
                feature_paths[stores[-1].instance_id] = args.packages.parent / package["features"]
    elif any((args.workspace, args.hierarchy, args.content)):
        if not all((args.workspace, args.hierarchy, args.content)):
            parser.error("--workspace, --hierarchy and --content must be supplied together")
        store = ContentStore(Workspace.from_json(args.workspace.read_text()),
                             json.loads(args.hierarchy.read_text()), args.content,
                             executor=generation_executor,
                             generation_strategy=args.generation_strategy)
    else:
        store = create_demo(args.state_dir)
        store.model_executor = generation_executor
        store.runtime = generation_executor.run_json if generation_executor else None
    if not args.packages:
        stores = [store]
        if args.features:
            feature_paths[store.instance_id] = args.features
    from .policies import library_policy
    policy = library_policy(stores, feature_paths) if args.recommendation_policy == "structural" else None
    service = ReaderService(stores, args.state_dir / "readers.json", recommendation_policy=policy)
    import uvicorn
    try:
        uvicorn.run(create_app(service, static_dir=args.static_dir), host="127.0.0.1", port=args.port)
    finally:
        service.close()


if __name__ == "__main__":
    main()
