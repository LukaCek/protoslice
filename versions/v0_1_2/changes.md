# Version v0.1.2 Changes

## Summary
Major enhancement to process configuration system with support for dynamic overrides, improved error handling, and automatic support generation detection.

---

## New Features

### 1. Process Override System
- **File**: `routes.py`
- **Function**: `create_temp_process(base_process_path: str, overrides: dict) -> (str, dict)`
- **Description**: Creates temporary process JSON files with runtime overrides
- **Supported Overrides**:
  - `supports`: Boolean - Enable/disable support generation
  - `layer_height`: String (e.g., "0.2mm") - Override layer height
- **Behavior**: 
  - Merges overrides into base process JSON
  - Sets multiple support-related keys for compatibility (`support_enable`, `support.enable`, `generate_support`, `support_generate`)
  - Returns temp file path and applied overrides dict

### 2. Enhanced OrcaSlicer Execution
- **Function**: `run_orcaslicer()` signature changed
  - **Old**: `run_orcaslicer(input_file_path, process, filament) -> bool`
  - **New**: `run_orcaslicer(input_file_path, process, filament, settings=None) -> dict`
- **New Parameters**:
  - `settings`: Optional dict with `supports` and `layer_height` overrides
- **Return Value**: Structured dict with keys:
  - `success`: Boolean
  - `stdout`: OrcaSlicer stdout
  - `stderr`: OrcaSlicer stderr
  - `error`: Error dict with `code` and `message` (on failure)
  - `process_used`: Path to process file used
  - `applied_overrides`: Dict of overrides that were applied

### 3. Automatic Retry with Supports
- **Trigger**: When Orca result contains warning suggesting "enable support"
- **Behavior**: Automatically retries slicing with `supports: True`
- **Response Fields**:
  - `auto_enabled_supports`: Boolean indicating if auto-retry occurred
  - `auto_retry_info`: Dict with retry details (`attempted`, `retry_success`, `retry_stdout`, `retry_stderr`, etc.)

### 4. JSON Type Error Recovery
- **Function**: `parse_invalid_type_keys(output_text: str) -> set`
- **Function**: `try_alternate_types(failing_keys, desired_bool) -> (dict|None, dict, str|None)`
- **Description**: When OrcaSlicer rejects JSON boolean types, automatically tries alternate representations:
  - Candidate values: `True`, `1`, `"1"`, `"true"`
- **Applies to**: Support-related keys (`generate_support`, `support_generate`, `support_enable`, `support`)

### 5. Optional 3MF File Download
- **Form Parameter**: `include_3mf` (boolean)
- **Behavior**: When true, returns the generated `.3mf` file as attachment instead of JSON metadata
- **Content-Type**: `application/octet-stream`
- **Download Name**: `output.3mf`

---

## API Changes

### Request Parameters (POST /)
- **New Parameters**:
  - `supports`: Top-level form field (alternative to `settings.supports`)
  - `settings`: JSON object with `supports` and `layer_height`
  - `include_3mf`: Boolean to request 3mf file download
- **Settings Validation**:
  - Only `supports` and `layer_height` keys allowed
  - Returns 400 error for unsupported keys
  - `supports` normalized to boolean
  - `layer_height` normalized to "X.XXmm" format

### Response Format (POST /)
- **Old Format**: Direct JSON or error tuple
- **New Format**: Always structured JSON
```json
{
  "success": true,
  "metadata": {
    "used_mm": float,
    "used_cm3": float,
    "max_z": float,
    "filament_type": string,
    "default_print_profile": string,
    "model_time": float (minutes),
    "total_time": float (minutes),
    "first_layer_time": float (minutes)
  },
  "orca": {
    "stdout": string,
    "stderr": string
  },
  "result_json": object (full Orca result),
  "process_used": string (path to process file),
  "applied_overrides": object,
  "auto_enabled_supports": boolean,
  "auto_retry_info": object
}
```

### Error Response Format
```json
{
  "success": false,
  "error": {
    "code": string,
    "message": string,
    "stdout": string (optional),
    "stderr": string (optional)
  }
}
```
- **Error Codes**:
  - `orca_missing`: OrcaSlicer binary not found
  - `missing_process`: Process config file not found
  - `missing_filament`: Filament config file not found
  - `process_override_failed`: Failed to create temp process file
  - `timeout`: OrcaSlicer timed out
  - `failed_after_retries`: All retry attempts exhausted
  - `missing_output`: Output 3mf file not found
  - `bad_request`: Settings must be JSON object
  - `bad_settings`: Unsupported settings keys provided

---

## Code Changes

### New Imports
```python
import base64          # For 3mf base64 encoding in debug mode
import shutil          # For which() to find OrcaSlicer binary
import tempfile        # (imported, not currently used)
import uuid            # For temp process file naming
from flask import current_app  # For debug mode detection
```

### Modified Functions

#### `run_orcaslicer()`
- **Lines**: 68-246 (significantly expanded from ~60 lines)
- **Changes**:
  - Uses `ORCASLICER_BIN` environment variable (default: 'orcaslicer')
  - Validates binary exists with `shutil.which()`
  - Creates temp process files when settings provided
  - Implements retry loop with max 3 retries
  - Implements arrange/orient fallback combinations
  - Timeout from `ORCA_TIMEOUT` env var (default: 300s)
  - Structured error returns with codes

#### `get_data_from_orcaslicer_output()`
- **Lines**: 247-291
- **Changes**:
  - Now raises `FileNotFoundError` instead of returning error tuple
  - Uses `.get()` with defaults for all regex searches (more robust)
  - Returns `filament_type` key (renamed from `type`)

#### `home()` (POST handler)
- **Lines**: 305-503
- **Changes**:
  - Added convenience `supports` form field handling
  - Added settings validation
  - Added layer_height format normalization
  - Added result.json validation
  - Added auto-retry logic for support suggestions
  - Added `include_3mf` handling
  - Added debug mode base64 3mf inclusion
  - Always returns structured JSON

---

## Environment Variables
- **`ORCASLICER_BIN`**: Path to OrcaSlicer binary (default: 'orcaslicer')
- **`ORCA_TIMEOUT`**: Timeout in seconds for OrcaSlicer execution (default: 300)

---

## Backward Compatibility Notes
- Default API version in `app.py` changed to `/v0.1.2`
- Response format changed significantly - clients expecting old format need updating
- All previous versions (`v0.1`, `v0.1.1`) remain accessible at their respective URL prefixes
- GET request handler unchanged (still renders index.html template)

---

## File Structure
```
versions/v0_1_2/
├── routes.py          # Main route handlers (534 lines)
├── __pycache__/       # Python cache
└── changes.md         # This file
```
