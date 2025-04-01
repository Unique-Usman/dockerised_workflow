from flask import Flask, render_template, request, jsonify, send_file
import os
import tempfile
import subprocess
import uuid
import json
import threading
import time
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
RESULTS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
ALLOWED_EXTENSIONS = {'nii.gz', 'nii', 'bval', 'bvec', 'json'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

# Store job status information
jobs = {}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files[]' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    files = request.files.getlist('files[]')
    if len(files) == 0:
        return jsonify({'error': 'No selected file'}), 400
    
    job_id = str(uuid.uuid4())
    job_folder = os.path.join(UPLOAD_FOLDER, job_id)
    os.makedirs(job_folder, exist_ok=True)
    
    saved_files = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(job_folder, filename)
            file.save(file_path)
            saved_files.append(file_path)
    
    jobs[job_id] = {
        'status': 'uploaded',
        'files': saved_files,
        'created_at': time.time(),
        'output_files': []
    }
    
    return jsonify({
        'job_id': job_id,
        'status': 'uploaded',
        'message': f'Successfully uploaded {len(saved_files)} files'
    })

@app.route('/api/process', methods=['POST'])
def process_files():
    data = request.json
    job_id = data.get('job_id')
    algorithm = data.get('algorithm', 'OJ_GU_seg')
    
    if not job_id or job_id not in jobs:
        return jsonify({'error': 'Invalid job ID'}), 400
    
    # Find the necessary files
    job = jobs[job_id]
    nifti_file = None
    bvec_file = None
    bval_file = None
    
    for file in job['files']:
        if file.endswith('.nii.gz') or file.endswith('.nii'):
            nifti_file = file
        elif file.endswith('.bvec'):
            bvec_file = file
        elif file.endswith('.bval'):
            bval_file = file
    
    if not all([nifti_file, bvec_file, bval_file]):
        return jsonify({'error': 'Missing required files (NIfTI, BVEC, BVAL)'}), 400
    
    # Update job status
    job['status'] = 'processing'
    
    # Start processing in a background thread
    result_folder = os.path.join(RESULTS_FOLDER, job_id)
    os.makedirs(result_folder, exist_ok=True)
    
    def run_processing():
        try:
            # Build the command
            cmd = [
                'python', '-m', 'WrapImage.nifti_wrapper',
                nifti_file, bvec_file, bval_file,
                '--algorithm', algorithm
            ]
            
            # Set working directory to result folder
            process = subprocess.Popen(
                cmd,
                cwd=result_folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                output_files = []
                for filename in ['f.nii.gz', 'dp.nii.gz', 'd.nii.gz']:
                    file_path = os.path.join(result_folder, filename)
                    if os.path.exists(file_path):
                        output_files.append(file_path)
                
                job['status'] = 'completed'
                job['output_files'] = output_files
                job['completed_at'] = time.time()
            else:
                job['status'] = 'failed'
                job['error'] = stderr
                job['completed_at'] = time.time()
        except Exception as e:
            job['status'] = 'failed'
            job['error'] = str(e)
            job['completed_at'] = time.time()
    
    processing_thread = threading.Thread(target=run_processing)
    processing_thread.start()
    
    return jsonify({
        'job_id': job_id,
        'status': 'processing',
        'message': 'Processing started'
    })

@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    response = {
        'job_id': job_id,
        'status': job['status'],
        'created_at': job['created_at']
    }
    
    if job['status'] == 'completed':
        response['output_files'] = [os.path.basename(f) for f in job['output_files']]
        response['completed_at'] = job.get('completed_at')
    elif job['status'] == 'failed':
        response['error'] = job.get('error', 'Unknown error')
        response['completed_at'] = job.get('completed_at')
    
    return jsonify(response)

@app.route('/api/results/<job_id>/<filename>', methods=['GET'])
def get_result_file(job_id, filename):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    if job['status'] != 'completed':
        return jsonify({'error': 'Job not completed'}), 400
    
    file_path = os.path.join(RESULTS_FOLDER, job_id, filename)
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(file_path, as_attachment=True)

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    job_list = []
    for job_id, job in jobs.items():
        job_list.append({
            'job_id': job_id,
            'status': job['status'],
            'created_at': job['created_at'],
            'completed_at': job.get('completed_at')
        })
    
    # Sort by creation time, most recent first
    job_list.sort(key=lambda x: x['created_at'], reverse=True)
    
    return jsonify({'jobs': job_list})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
