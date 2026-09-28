# Business Management System — Page Structure

## Public Pages

- Login
- Register

## Application

### Dashboard
- Dashboard

### Users
- Users List
- Create User
- Edit User

### Branches
- Branches List
- Create Branch
- Edit Branch
- Branch Details

### Employees
- Employees List
- Create Employee
- Edit Employee
- Employee Profile

### Shifts
- Shift Schedule
- Create Shift
- Edit Shift

### Products
- Products List
- Create Product
- Edit Product
- Product Details

### Categories
- Categories List
- Create Category
- Edit Category

### Inventory
- Inventory Overview
- Stock In
- Stock Write-Off
- Inventory Transaction History

### Sales
- New Sale
- Sales History
- Sale Details

### Customers
- Customers List
- Create Customer
- Edit Customer
- Customer Details
- Customer Purchase History

### Finance
- Income and Expenses
- Add Income
- Add Expense
- Expense Categories

### Analytics
- Dashboard and Analytics
- Branch Comparison

### Notifications
- Notification Center

### Audit
- Audit Log

### Account
- My Profile
- Change Password
- My Shifts



## Navigation Flow

### Authentication

Login
→ Dashboard

Register
→ Login
→ Dashboard

### Main Navigation

Dashboard
├── Users
│   ├── Users List
│   ├── Create User
│   └── Edit User
│
├── Branches
│   ├── Branches List
│   ├── Create Branch
│   └── Branch Details
│       └── Edit Branch
│
├── Employees
│   ├── Employees List
│   ├── Create Employee
│   └── Employee Profile
│       └── Edit Employee
│
├── Shifts
│   ├── Shift Schedule
│   ├── Create Shift
│   └── Edit Shift
│
├── Products
│   ├── Products List
│   ├── Create Product
│   └── Product Details
│       └── Edit Product
│
├── Categories
│   ├── Categories List
│   ├── Create Category
│   └── Edit Category
│
├── Inventory
│   ├── Inventory Overview
│   ├── Stock In
│   ├── Stock Write-Off
│   └── Inventory Transaction History
│
├── Sales
│   ├── New Sale
│   └── Sales History
│       └── Sale Details
│
├── Customers
│   ├── Customers List
│   ├── Create Customer
│   └── Customer Details
│       ├── Edit Customer
│       └── Customer Purchase History
│
├── Finance
│   ├── Income and Expenses
│   ├── Add Income
│   ├── Add Expense
│   └── Expense Categories
│
├── Analytics
│   ├── Dashboard and Analytics
│   └── Branch Comparison
│
├── Notifications
│   └── Notification Center
│
├── Audit
│   └── Audit Log
│
└── Account
    ├── My Profile
    ├── Change Password
    └── My Shifts

## Navigation by Role

### Admin

Dashboard
Users
Branches
Employees
Shifts
Products
Categories
Inventory
Sales
Customers
Finance
Analytics
Notifications
Audit Log
Profile

### Manager

Dashboard
Employees
Shifts
Products
Categories
Inventory
Sales
Customers
Analytics
Profile

### Employee

Sales
My Shifts
Products (read-only)
Profile