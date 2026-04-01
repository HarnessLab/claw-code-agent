import React, { useState, useEffect } from 'react';
import ProductList from '../../components/ProductList/ProductList';
import './Products.css';

const Products = () => {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState('all');

  // Mock data for products
  const mockProducts = [
    { id: 1, name: 'Wireless Headphones', description: 'High-quality wireless headphones with noise cancellation', price: 129.99, image: 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'electronics' },
    { id: 2, name: 'Smart Watch', description: 'Feature-rich smartwatch with health monitoring', price: 199.99, image: 'https://images.unsplash.com/photo-1523275335684-37898b6baf30?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'electronics' },
    { id: 3, name: 'Cotton T-Shirt', description: 'Comfortable cotton t-shirt for everyday wear', price: 24.99, image: 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'clothing' },
    { id: 4, name: 'Coffee Maker', description: 'Automatic coffee maker with programmable timer', price: 89.99, image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'home-kitchen' },
    { id: 5, name: 'Bestseller Novel', description: 'Award-winning novel by popular author', price: 14.99, image: 'https://images.unsplash.com/photo-1544947950-fa07a98d237f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'books' },
    { id: 6, name: 'Bluetooth Speaker', description: 'Portable speaker with excellent sound quality', price: 79.99, image: 'https://images.unsplash.com/photo-1546868871-7041f2a55e12?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'electronics' },
    { id: 7, name: 'Jeans', description: 'Classic fit jeans for casual wear', price: 59.99, image: 'https://images.unsplash.com/photo-1541099649105-f69ad21f3246?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'clothing' },
    { id: 8, name: 'Cookware Set', description: 'Complete cookware set for home cooking', price: 149.99, image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80', category: 'home-kitchen' },
  ];

  const categories = [
    { id: 'all', name: 'All Products' },
    { id: 'electronics', name: 'Electronics' },
    { id: 'clothing', name: 'Clothing' },
    { id: 'home-kitchen', name: 'Home & Kitchen' },
    { id: 'books', name: 'Books' },
  ];

  useEffect(() => {
    // Simulate API call
    const fetchProducts = () => {
      try {
        setTimeout(() => {
          setProducts(mockProducts);
          setLoading(false);
        }, 500);
      } catch (err) {
        setError('Failed to load products');
        setLoading(false);
      }
    };

    fetchProducts();
  }, []);

  const filteredProducts = selectedCategory === 'all' 
    ? products 
    : products.filter(product => product.category === selectedCategory);

  if (loading) {
    return <div className="loading">Loading products...</div>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  return (
    <div className="products-page">
      <div className="container">
        <h1 className="page-title">Our Products</h1>
        
        <div className="filters">
          <h3>Filter by Category:</h3>
          <div className="category-filters">
            {categories.map(category => (
              <button
                key={category.id}
                className={`filter-btn ${selectedCategory === category.id ? 'active' : ''}`}
                onClick={() => setSelectedCategory(category.id)}
              >
                {category.name}
              </button>
            ))}
          </div>
        </div>
        
        <ProductList products={filteredProducts} />
      </div>
    </div>
  );
};

export default Products;