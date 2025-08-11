from app import create_app
from models import db, Product, User
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    if not User.query.filter_by(email='admin@example.com').first():
        admin = User(name='Admin', email='admin@example.com', password_hash=generate_password_hash('admin123'), is_admin=True, wallet=0.0)
        db.session.add(admin)
    if not User.query.filter_by(email='pooja@example.com').first():
        u = User(name='Pooja', email='pooja@example.com', password_hash=generate_password_hash('user123'), wallet=100.0)
        db.session.add(u)
    if Product.query.count() == 0:
        p1 = Product(name='Samosa', description='Crispy samosa', price=20.0, available=True)
        p2 = Product(name='Fried Rice', description='Veg fried rice', price=60.0, available=True)
        p3 = Product(name='Tea', description='Hot tea', price=10.0, available=True)
        db.session.add_all([p1,p2,p3])
    db.session.commit()
    print("Seeded data")
