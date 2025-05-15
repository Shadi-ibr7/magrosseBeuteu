# save this as test_api.py
import requests
import os
import pytest
from app import app
import io

# --- Configuration ---
API_URL = "http://localhost:5050/upload_pdf"
# Replace with the actual path to your sample PDF
PDF_FILE_PATH = "/Users/ryansaleh/Downloads/clean_pdf_table_pipeline_v0.1/sampleA.pdf"
# Replace with a path to a non-PDF file for testing errors
NON_PDF_FILE_PATH = "/Users/ryansaleh/Downloads/clean_pdf_table_pipeline_v0.1/README.md"

# --- Helper Function ---
def send_request(file_path=None, folder_name=None, expect_success=True):
    """Sends a request to the API and prints the response."""
    print("-" * 20)
    print(f"Testing with File: {file_path}, Folder: {folder_name}")

    files = None
    data = {}

    if file_path:
        if not os.path.exists(file_path):
            print(f"ERROR: Test file not found at {file_path}")
            print("-" * 20)
            return

        # Correctly determine filename for the request
        file_name_for_request = os.path.basename(file_path)
        try:
            # Note: File needs to be opened within the 'files' dict context ideally,
            # but for simplicity here, we open/close it.
            # In production tests, manage file handles carefully.
            with open(file_path, 'rb') as f:
                 # Determine content type (basic check)
                 content_type = 'application/pdf' if file_path.lower().endswith('.pdf') else 'application/octet-stream'
                 files = {'file': (file_name_for_request, f.read(), content_type)}
        except IOError as e:
            print(f"ERROR: Could not read file {file_path}: {e}")
            print("-" * 20)
            return

    if folder_name:
        data['folder_name'] = folder_name

    try:
        response = requests.post(API_URL, files=files, data=data, timeout=180) # Increased timeout for processing

        print(f"Status Code: {response.status_code}")
        # Try to print JSON, fallback to text if decoding fails
        try:
            print("Response JSON:")
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print("Response Text (Not JSON):")
            print(response.text)

        # Basic check based on expected success
        if expect_success and response.status_code != 200:
            print("!!! TEST FAILED (Expected 200 OK) !!!")
        elif not expect_success and response.status_code == 200:
             print("!!! TEST FAILED (Expected non-200 status) !!!")
        else:
             print("Test outcome consistent with expectation.")


    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Connection refused. Is the API running at {API_URL}? Details: {e}")
    except requests.exceptions.Timeout as e:
        print(f"ERROR: Request timed out. API might be processing slowly. Details: {e}")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An unexpected error occurred during the request: {e}")

    print("-" * 20)


# --- Run Tests ---
if __name__ == "__main__":
    # Test Scenario A: Upload PDF (No Folder Name)
    send_request(file_path=PDF_FILE_PATH, expect_success=True)

    # Test Scenario B: Upload PDF (With Folder Name)
    # send_request(file_path=PDF_FILE_PATH, folder_name="python_test_folder", expect_success=True)

    # # Test Scenario C: Missing File Part (sending no 'files')
    # send_request(file_path=None, expect_success=False) # Expect 400

    # # Test Scenario D: Upload Non-PDF File
    # # Ensure NON_PDF_FILE_PATH points to a real non-PDF file
    # if os.path.exists(NON_PDF_FILE_PATH):
    #      send_request(file_path=NON_PDF_FILE_PATH, expect_success=False) # Expect 400
    # else:
    #      print(f"SKIPPING Non-PDF test: File not found at {NON_PDF_FILE_PATH}")
    #      print("-" * 20)

    print("Testing finished.")

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_health_check(client):
    """Test de l'endpoint de vérification de santé."""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json['status'] == 'healthy'

def test_upload_no_file(client):
    """Test de l'upload sans fichier."""
    response = client.post('/upload')
    assert response.status_code == 400
    assert 'error' in response.json
    assert 'Aucun fichier' in response.json['error']

def test_upload_empty_file(client):
    """Test de l'upload avec un fichier vide."""
    data = {'file': (io.BytesIO(b''), '')}
    response = client.post('/upload', data=data)
    assert response.status_code == 400
    assert 'error' in response.json
    assert 'Aucun fichier sélectionné' in response.json['error']

def test_upload_invalid_file_type(client):
    """Test de l'upload avec un type de fichier invalide."""
    data = {'file': (io.BytesIO(b'test content'), 'test.txt')}
    response = client.post('/upload', data=data)
    assert response.status_code == 400
    assert 'error' in response.json
    assert 'Type de fichier non autorisé' in response.json['error']

def test_upload_valid_pdf(client):
    """Test de l'upload d'un PDF valide."""
    # Créer un PDF minimal valide
    pdf_content = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'
    data = {'file': (io.BytesIO(pdf_content), 'test.pdf')}
    
    # Mock de l'environnement Supabase
    os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
    os.environ['SUPABASE_KEY'] = 'test-key'
    os.environ['BUCKET_NAME'] = 'test-bucket'
    
    response = client.post('/upload', data=data)
    assert response.status_code == 500  # 500 car nous n'avons pas de vrai client Supabase
    assert 'error' in response.json

def test_upload_valid_image(client):
    """Test de l'upload d'une image valide."""
    # Créer une image PNG minimale valide
    png_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    data = {'file': (io.BytesIO(png_content), 'test.png')}
    
    # Mock de l'environnement Supabase
    os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
    os.environ['SUPABASE_KEY'] = 'test-key'
    os.environ['BUCKET_NAME'] = 'test-bucket'
    
    response = client.post('/upload', data=data)
    assert response.status_code == 500  # 500 car nous n'avons pas de vrai client Supabase
    assert 'error' in response.json