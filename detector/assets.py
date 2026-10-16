"""Optional cache of the AI's probability maps in Earth Engine asset storage.

Running the classifier over a whole site takes 1-2 minutes per image. With
asset storage, each map is computed once (as a batch export, which has no
interactive time limit), saved in the user's Earth Engine project, and
re-used on later runs. Each asset is tagged with a `cache_key` covering the
trained model, the radar scenes and the pixel grid; if any of these change,
the map is rebuilt. Without asset storage the pipeline computes the maps
directly instead (slower, same result).
"""
import time

import ee

FOLDER = "jamuna_bank_watch"


def folder(project):
    return f"projects/{project}/assets/{FOLDER}"


def asset_id(project, run_id, site_id):
    return f"{folder(project)}/probs_{run_id}_{site_id}"


def available(project):
    """True if the project's asset folder exists (or can be created)."""
    path = folder(project)
    try:
        ee.data.getAsset(path)
        return True
    except ee.EEException:
        pass
    try:
        ee.data.createFolder(path)
        return True
    except ee.EEException:
        return False


def _cache_key_of(aid):
    try:
        return ee.data.getAsset(aid).get("properties", {}).get("cache_key")
    except ee.EEException:
        return None


def ensure(jobs, poll_s=15):
    """Make sure each job's asset exists with the right cache_key.

    jobs: list of {"aid", "image", "grid", "key", "name"}. Starts all missing
    exports at once, then waits for them. Returns the names that were rebuilt.
    """
    running = []
    for j in jobs:
        current = _cache_key_of(j["aid"])
        if current == j["key"]:
            print(f"   {j['name']}: AI map already saved, re-using it")
            continue
        if current is not None:
            ee.data.deleteAsset(j["aid"])  # our own asset, from an older model or scene
        g = j["grid"]
        t = g.ee_grid()["affineTransform"]
        task = ee.batch.Export.image.toAsset(
            image=j["image"].toByte().set("cache_key", j["key"]),
            description=f"jbw_{j['name']}", assetId=j["aid"],
            crs="EPSG:4326",
            crsTransform=[t["scaleX"], 0, t["translateX"], 0, t["scaleY"], t["translateY"]],
            dimensions=f"{g.width}x{g.height}",
            maxPixels=1e10, pyramidingPolicy={".default": "sample"})
        task.start()
        running.append((j["name"], task))
        print(f"   {j['name']}: computing AI map in Earth Engine (batch export)")

    t0 = time.time()
    while running:
        time.sleep(poll_s)
        still = []
        for name, task in running:
            st = task.status()
            if st["state"] == "COMPLETED":
                print(f"   {name}: saved ({time.time() - t0:.0f}s)")
            elif st["state"] in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"export {name} {st['state']}: {st.get('error_message')}")
            else:
                still.append((name, task))
        running = still
