"""Entry point launched by Script Toolkit in a separate Blender process."""

import json
import os
import sys
import traceback

SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)


def _job_path_from_args():
    if "--" not in sys.argv:
        raise RuntimeError("Worker expects a job JSON path after '--'.")
    arguments = sys.argv[sys.argv.index("--") + 1:]
    if not arguments:
        raise RuntimeError("Worker job JSON path is missing.")
    return os.path.abspath(arguments[0])


def _write_result(path, result):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def main():
    job_path = _job_path_from_args()
    with open(job_path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    result_path = job["result_path"]
    result = {"status": "running", "message": "Worker started", "total": 0, "success": 0, "failed": []}
    _write_result(result_path, result)
    try:
        from worker_jobs import run_job
        run_job(job, result, lambda: _write_result(result_path, result))
        result["status"] = "completed"
        result["message"] = "Batch finished"
    except Exception as exc:
        result["status"] = "failed"
        result["message"] = str(exc)
        result["traceback"] = traceback.format_exc()
    _write_result(result_path, result)


if __name__ == "__main__":
    main()
