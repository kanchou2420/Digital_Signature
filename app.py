from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify, session
import sqlite3
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
import base64
import io
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-this-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Tạo thư mục uploads nếu chưa có
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Decorator kiểm tra đăng nhập
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Vui lòng đăng nhập để tiếp tục!', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Khởi tạo database
def init_db():
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    
    # Bảng người dùng với authentication
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL,
            signature_image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng file đã ký (liên kết với user_id thay vì signer_name)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            signed_filename TEXT NOT NULL,
            signature_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            signature_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Tạo cặp khóa RSA
def generate_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    public_key = private_key.public_key()
    
    # Chuyển đổi sang PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    return private_pem.decode('utf-8'), public_pem.decode('utf-8')

# Ký file
def sign_file(file_path, private_key_pem):
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(file_data)
        file_hash = digest.finalize()
        
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode('utf-8'),
            password=None,
            backend=default_backend()
        )
        
        signature = private_key.sign(
            file_hash,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return signature, file_hash
    except Exception as e:
        print(f"Lỗi khi ký file: {str(e)}")
        return None, None

# Xác minh chữ ký
def verify_signature(file_path, signature_data, public_key_pem):
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(file_data)
        file_hash = digest.finalize()
        
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode('utf-8'),
            backend=default_backend()
        )
        
        public_key.verify(
            signature_data,
            file_hash,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        print(f"Lỗi xác minh: {str(e)}")
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        full_name = request.form['full_name'].strip()
        email = request.form.get('email', '').strip()
        signature_image = request.form.get('signature_image', '')
        
        # Validation
        if not username or not password or not full_name:
            flash('Vui lòng điền đầy đủ thông tin bắt buộc!', 'error')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp!', 'error')
            return redirect(url_for('register'))
        
        if len(password) < 6:
            flash('Mật khẩu phải có ít nhất 6 ký tự!', 'error')
            return redirect(url_for('register'))
        
        # Tạo cặp khóa
        private_key, public_key = generate_key_pair()
        password_hash = generate_password_hash(password)
        
        try:
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (username, password_hash, full_name, email, private_key, public_key, signature_image)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (username, password_hash, full_name, email, private_key, public_key, signature_image))
            conn.commit()
            conn.close()
            
            flash(f'Đăng ký thành công! Hãy đăng nhập để tiếp tục.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Tên đăng nhập này đã tồn tại!', 'error')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        
        conn = sqlite3.connect('digital_signature.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password_hash, full_name FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username
            session['full_name'] = user[2]
            flash(f'Chào mừng {user[2]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Tên đăng nhập hoặc mật khẩu không đúng!', 'error')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Đã đăng xuất thành công!', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    
    # Lấy thông tin user
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    # Lấy danh sách file đã ký
    cursor.execute('''
        SELECT * FROM signed_files 
        WHERE user_id = ? 
        ORDER BY created_at DESC 
        LIMIT 10
    ''', (user_id,))
    recent_files = cursor.fetchall()
    
    # Đếm tổng số file
    cursor.execute('SELECT COUNT(*) FROM signed_files WHERE user_id = ?', (user_id,))
    total_files = cursor.fetchone()[0]
    
    conn.close()
    
    return render_template('dashboard.html', user=user, recent_files=recent_files, total_files=total_files)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session['user_id']
    
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        email = request.form.get('email', '').strip()
        signature_image = request.form.get('signature_image', '')
        
        # Cập nhật mật khẩu nếu có
        new_password = request.form.get('new_password', '')
        if new_password:
            if len(new_password) < 6:
                flash('Mật khẩu mới phải có ít nhất 6 ký tự!', 'error')
                return redirect(url_for('profile'))
            
            password_hash = generate_password_hash(new_password)
            
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET full_name = ?, email = ?, signature_image = ?, password_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (full_name, email, signature_image, password_hash, user_id))
            conn.commit()
            conn.close()
            
            flash('Cập nhật thông tin và mật khẩu thành công!', 'success')
        else:
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET full_name = ?, email = ?, signature_image = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (full_name, email, signature_image, user_id))
            conn.commit()
            conn.close()
            
            flash('Cập nhật thông tin thành công!', 'success')
        
        # Cập nhật session
        session['full_name'] = full_name
        return redirect(url_for('profile'))
    
    # GET request
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    return render_template('profile.html', user=user)

@app.route('/keys')
@login_required
def view_keys():
    user_id = session['user_id']
    
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    return render_template('keys.html', user=user)

@app.route('/sign', methods=['GET', 'POST'])
@login_required
def sign_file_route():
    user_id = session['user_id']
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('sign_file_route'))
        
        file = request.files['file']
        if file.filename == '':
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('sign_file_route'))
        
        # Lấy private key của user
        conn = sqlite3.connect('digital_signature.db')
        cursor = conn.cursor()
        cursor.execute('SELECT private_key FROM users WHERE id = ?', (user_id,))
        result = cursor.fetchone()
        
        if not result:
            flash('Không tìm thấy thông tin người dùng!', 'error')
            conn.close()
            return redirect(url_for('sign_file_route'))
        
        private_key = result[0]
        
        # Lưu file
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        
        # Ký file
        signature, file_hash = sign_file(file_path, private_key)
        
        if signature:
            signature_filename = f"{unique_filename}.sig"
            signature_path = os.path.join(app.config['UPLOAD_FOLDER'], signature_filename)
            
            with open(signature_path, 'wb') as sig_file:
                sig_file.write(signature)
            
            cursor.execute('''
                INSERT INTO signed_files 
                (user_id, original_filename, signed_filename, signature_filename, file_hash, signature_data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, filename, unique_filename, signature_filename,
                  base64.b64encode(file_hash).decode('utf-8'),
                  base64.b64encode(signature).decode('utf-8')))
            conn.commit()
            
            flash('Ký file thành công!', 'success')
        else:
            flash('Lỗi khi ký file!', 'error')
        
        conn.close()
        return redirect(url_for('signed_files'))
    
    return render_template('sign.html')

@app.route('/files')
@login_required
def signed_files():
    user_id = session['user_id']
    
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sf.*, u.full_name, u.public_key, u.username
        FROM signed_files sf 
        JOIN users u ON sf.user_id = u.id 
        WHERE sf.user_id = ?
        ORDER BY sf.created_at DESC
    ''', (user_id,))
    files = cursor.fetchall()
    conn.close()
    
    return render_template('files.html', files=files)

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        if 'file' not in request.files or 'public_key' not in request.form:
            flash('Vui lòng cung cấp đầy đủ thông tin!', 'error')
            return redirect(url_for('verify'))
        
        file = request.files['file']
        public_key = request.form['public_key']
        signature_b64 = request.form.get('signature', '')
        
        if file.filename == '':
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('verify'))
        
        try:
            signature_data = base64.b64decode(signature_b64)
        except:
            flash('Chữ ký không hợp lệ!', 'error')
            return redirect(url_for('verify'))
        
        # Lưu file tạm
        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        file.save(temp_path)
        
        # Xác minh
        is_valid = verify_signature(temp_path, signature_data, public_key)
        
        # Xóa file tạm
        os.remove(temp_path)
        
        if is_valid:
            flash('Chữ ký hợp lệ!', 'success')
        else:
            flash('Chữ ký không hợp lệ!', 'error')
    
    # Lấy danh sách user để chọn public key
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username, full_name, public_key FROM users ORDER BY full_name')
    users = cursor.fetchall()
    conn.close()
    
    return render_template('verify.html', users=users)

@app.route('/api/signer/<signer_name>')
def get_signer_info(signer_name):
    """API endpoint để lấy thông tin public key của người ký"""
    try:
        conn = sqlite3.connect('digital_signature.db')
        cursor = conn.cursor()
        
        # Tìm user theo username
        cursor.execute('SELECT public_key, full_name FROM users WHERE username = ?', (signer_name,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return jsonify({
                'success': True,
                'public_key': result[0],
                'full_name': result[1]
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy người ký'
            }), 404
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi: {str(e)}'
        }), 500
    
@app.route('/download/<file_type>/<filename>')
@login_required
def download_file(file_type, filename):
    try:
        if file_type == 'original':
            return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)
        elif file_type == 'signature':
            return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)
        elif file_type == 'public_key':
            user_id = session['user_id']
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('SELECT public_key, username FROM users WHERE id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                public_key = result[0]
                username = result[1]
                temp_file = io.BytesIO(public_key.encode('utf-8'))
                return send_file(temp_file, as_attachment=True, 
                               download_name=f"{username}_public_key.pem",
                               mimetype='text/plain')
    except Exception as e:
        flash(f'Lỗi tải file: {str(e)}', 'error')
    
    return redirect(url_for('signed_files'))

# Endpoint để url_for('400') và url_for('500') hoạt động
@app.route('/400', endpoint='400')
def page_400():
    return render_template('400.html'), 400

@app.route('/500', endpoint='500')
def page_500():
    return render_template('500.html'), 500

# Đăng ký error handlers để trả về cùng template khi xảy ra lỗi thực tế
@app.errorhandler(400)
def bad_request(e):
    return render_template('400.html'), 400

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True)