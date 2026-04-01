import React, { useState, useEffect } from 'react';
import Hero from '../../components/Hero/Hero';
import Category from '../../components/Category/Category';
import ProductList from '../../components/ProductList/ProductList';
import './Home.css';

const Home = () => {
  const [categories] = useState([
    { id: 1, name: 'Electronics', slug: 'electronics', image: 'https://images.unsplash.com/photo-1546868871-7041f2a55e12?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 2, name: 'Clothing', slug: 'clothing', image: 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 3, name: 'Home & Kitchen', slug: 'home-kitchen', image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 4, name: 'Books', slug: 'books', image: 'https://images.unsplash.com/photo-1544947950-fa07a98d237f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
  ]);

  const [featuredProducts] = useState([
    { id: 1, name: 'Wireless Headphones', description: 'High-quality wireless headphones with noise cancellation', price: 129.99, image: 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 2, name: 'Smart Watch', description: 'Feature-rich smartwatch with health monitoring', price: 199.99, image: 'https://images.unsplash.com/photo-1523275335684-37898b6baf30?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 3, name: 'Cotton T-Shirt', description: 'Comfortable cotton t-shirt for everyday wear', price: 24.99, image: 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
    { id: 4, name: 'Coffee Maker', description: 'Automatic coffee maker with programmable timer', price: 89.99, image: 'https://images.unsplash.com/photo-1556911220-e15b29be8c8f?ixlib=rb-4.0.3&auto=format&fit=crop&w=200&q=80' },
  ]);

  return (
    <div className="home">
      <Hero />
      
      <div className="container">
        <h2 className="section-title">Shop by Category</h2>
        <div className="categories">
          {categories.map(category => (
            <Category key={category.id} category={category} />
          ))}
        </div>
        
        <h2 className="section-title">Featured Products</h2>
        <ProductList products={featuredProducts} />
      </div>
    </div>
  );
};

export default Home;