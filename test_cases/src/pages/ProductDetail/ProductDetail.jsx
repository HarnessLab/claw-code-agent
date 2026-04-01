import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useCart } from '../../context/CartContext';
import './ProductDetail.css';

const ProductDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { addToCart } = useCart();
  const [product, setProduct] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [quantity, setQuantity] = useState(1);

  // Mock product data
  const mockProducts = [
    { id: 1, name: 'Wireless Headphones', description: 'High-quality wireless headphones with noise cancellation. Perfect for music lovers and professionals alike.', price: 129.99, image: 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'electronics' },
    { id: 2, name: 'Smart Watch', description: 'Feature-rich smartwatch with health monitoring, GPS tracking, and smartphone integration.', price: 199.99, image: 'https://images.unsplash.com/photo-1523275335684-37898b6baf30?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'electronics' },
    { id: 3, name: 'Cotton T-Shirt', description: 'Comfortable cotton t-shirt for everyday wear. Available in multiple colors and sizes.', price: 24.99, image: 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'clothing' },
    { id: 4, name: 'Coffee Maker', description: 'Automatic coffee maker with programmable timer and thermal carafe. Brew perfect coffee every time.', price: 89.99, image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'home-kitchen' },
    { id: 5, name: 'Bestseller Novel', description: 'Award-winning novel by popular author. A captivating story that keeps readers engaged from start to finish.', price: 14.99, image: 'https://images.unsplash.com/photo-1544947950-fa07a98d237f?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'books' },
    { id: 6, name: 'Bluetooth Speaker', description: 'Portable speaker with excellent sound quality and long battery life. Perfect for outdoor adventures.', price: 79.99, image: 'https://images.unsplash.com/photo-1546868871-7041f2a55e12?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'electronics' },
    { id: 7, name: 'Jeans', description: 'Classic fit jeans for casual wear. Made with premium denim for comfort and durability.', price: 59.99, image: 'https://images.unsplash.com/photo-1541099649105-f69ad21f3246?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'clothing' },
    { id: 8, name: 'Cookware Set', description: 'Complete cookware set for home cooking. Non-stick coating for easy cleaning and healthy cooking.', price: 149.99, image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80', category: 'home-kitchen' },
  ];

  useEffect(() => {
    // Simulate API call
    const fetchProduct = () => {
      try {
        setTimeout(() => {
          const foundProduct = mockProducts.find(p => p.id === parseInt(id));
          if (foundProduct) {
            setProduct(foundProduct);
          } else {
            setError('Product not found');
          }
          setLoading(false);
        }, 500);
      } catch (err) {
        setError('Failed to load product');
        setLoading(false);
      }
    };

    fetchProduct();
  }, [id]);

  const handleAddToCart = () => {
    addToCart(product, quantity);
    alert(`${quantity} ${product.name}(s) added to cart!`);
    navigate('/cart');
  };

  if (loading) {
    return <div className="loading">Loading product...</div>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  if (!product) {
    return <div className="error">Product not found</div>;
  }

  return (
    <div className="product-detail">
      <div className="container">
        <button onClick={() => navigate(-1)} className="back-btn">
          ← Back to Products
        </button>
        
        <div className="product-detail-content">
          <div className="product-image">
            <img src={product.image} alt={product.name} />
          </div>
          
          <div className="product-info">
            <h1 className="product-name">{product.name}</h1>
            <p className="product-category">{product.category}</p>
            <div className="product-price">${product.price.toFixed(2)}</div>
            <p className="product-description">{product.description}</p>
            
            <div className="quantity-selector">
              <label htmlFor="quantity">Quantity:</label>
              <div className="quantity-controls">
                <button 
                  onClick={() => setQuantity(Math.max(1, quantity - 1))}
                  className="quantity-btn"
                >
                  -
                </button>
                <span className="quantity-display">{quantity}</span>
                <button 
                  onClick={() => setQuantity(quantity + 1)}
                  className="quantity-btn"
                >
                  +
                </button>
              </div>
            </div>
            
            <button 
              onClick={handleAddToCart}
              className="add-to-cart-btn"
            >
              Add to Cart
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProductDetail;