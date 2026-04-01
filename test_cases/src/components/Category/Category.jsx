import React from 'react';
import { Link } from 'react-router-dom';
import './Category.css';

const Category = ({ category }) => {
  return (
    <div className="category">
      <Link to={`/products?category=${category.slug}`}>
        <div className="category-image">
          <img src={category.image} alt={category.name} />
        </div>
        <h3 className="category-name">{category.name}</h3>
      </Link>
    </div>
  );
};

export default Category;