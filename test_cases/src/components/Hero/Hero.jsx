import React from 'react';
import { Link } from 'react-router-dom';
import './Hero.css';

const Hero = () => {
  return (
    <section className="hero">
      <div className="hero-content">
        <h1>Welcome to ShopEasy</h1>
        <p>Your one-stop destination for all your shopping needs</p>
        <Link to="/products" className="cta-button">
          Shop Now
        </Link>
      </div>
    </section>
  );
};

export default Hero;