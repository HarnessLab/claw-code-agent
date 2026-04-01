# Modern E-Commerce Store

A production-ready e-commerce application with responsive design, authentication, and full shopping functionality.

## Features

- Responsive UI with mobile-first design
- Homepage with hero section and featured products
- Category browsing
- Product cards with images and details
- Product detail page
- Shopping cart functionality
- Checkout form with validation
- Authentication pages (login/signup)
- Reusable React components
- Mock backend/data layer
- Clean, modern styling
- Context API for state management

## Tech Stack

- React.js (with hooks and context API)
- React Router for navigation
- CSS Modules for styling
- LocalStorage for data persistence
- Mock API for backend simulation

## Getting Started

### Prerequisites

- Node.js (v14 or later)
- npm or yarn

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm start
   ```

### Running the Application

The app will be available at http://localhost:3000

## Project Structure

```
src/
├── components/
│   ├── Header/
│   ├── Footer/
│   ├── Hero/
│   ├── ProductCard/
│   ├── Category/
│   ├── CartItem/
│   └── ProductList/
├── pages/
│   ├── Home/
│   ├── Products/
│   ├── ProductDetail/
│   ├── Cart/
│   ├── Checkout/
│   ├── Login/
│   └── Register/
├── context/
│   ├── AuthContext.js
│   └── CartContext.js
├── styles/
│   └── globals.css
└── App.js
```

## Development

This project was built with modern React practices including:
- Component-based architecture
- Context API for state management
- Responsive design principles
- Form validation
- Mock data layer for demonstration

## Deployment

To build for production:
```bash
npm run build
```

The build artifacts will be stored in the `build` folder.

## License

This project is licensed under the MIT License.