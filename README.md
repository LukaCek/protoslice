# ProtoSlice

ProtoSlice is a web-based tool for estimating the cost and time of a 3D print. It uses OrcaSlicer to slice 3D models and extracts key information like estimated print time and material usage.

## Features

-   Upload a 3D model in STL format.
-   Select from a list of predefined filament and process profiles.
-   Get a detailed breakdown of the print, including:
    -   Filament usage (in mm and cm³).
    -   Model print time.
    -   Total estimated print time.
    -   First layer print time.
    -   Maximum Z height.

## Prerequisites

-   Python 3
-   [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer/releases) installed and available in your system's PATH.
-   The required printer, process and filament profiles in the `files` directory.

## Installation

### Oneline install:
```bash
sudo apt install docker.io git -y && git clone https://github.com/LukaCek/protoslice/ && cd protoslice && sudo ./deploy.sh
```

### Docker:

1.  Clone the repository:
    ```bash
    git clone https://github.com/your-username/protoslice.git
    cd protoslice
    ```

2.  Install docker:
    

3.  Build and run app in docker:
    ```bash
    ./deploy.sh
    ```

### Normal install:
1.  Clone the repository:
    ```bash
    git clone https://github.com/your-username/protoslice.git
    cd protoslice
    ```

2. Create python virtual envirament:
     ```bash
    python3 -m venv venv
    ```

3. Go to venv:
     ```bash
    source venv/bin/activate
    ```

## Usage

1.  Run the Flask application:
    ```bash
    python app.py
    ```

2.  Open your web browser and navigate to `http://localhost:5252`.

3.  Use the web form to upload your STL file and select the desired filament and process.

4.  Click "Submit" to see the print estimation.

## API

The application exposes a simple API. You can `POST` a multipart form to `/v0.1/` with the following fields:

-   `stlFile`: The STL file to be processed.
-   `filament`: The name of the filament profile file (e.g., `Bambu PETG Basic @BBL X1C.json`).
-   `process`: The name of the process profile file (e.g., `0.20mm Standard @BBL X1C.json`).

The API will return a JSON object with the slicing results.
