document.addEventListener('DOMContentLoaded', function () {
    const productCards = Array.from(document.querySelectorAll('[data-product-card]'));
    const searchInput = document.getElementById('pos-search');
    const noResults = document.getElementById('pos-no-results');
    const cartContainer = document.getElementById('pos-cart-lines');
    const emptyCart = document.getElementById('pos-cart-empty');
    const totalElement = document.getElementById('pos-total');
    const clearButton = document.getElementById('pos-clear-cart');
    const checkoutButton = document.getElementById('pos-checkout');
    const feedback = document.getElementById('pos-feedback');
    const paymentSelect = document.getElementById('pos-payment');
    const customerSelect = document.getElementById('pos-customer');
    const loyaltyPanel = document.getElementById('pos-loyalty-panel');
    const loyaltyBalance = document.getElementById('pos-loyalty-balance');
    const loyaltyMaximum = document.getElementById('pos-loyalty-maximum');
    const redeemPointsInput = document.getElementById('pos-redeem-points');
    const loyaltyError = document.getElementById('pos-loyalty-error');
    const posPage = document.querySelector('.pos-page');
    const cart = new Map();
    let isSubmitting = false;
    let quoteRequest = 0;

    if (!cartContainer || !emptyCart || !totalElement || !clearButton) {
        return;
    }

    const formatAmount = function (amount) {
        return amount.toFixed(2);
    };

    const updateCatalogButtons = function () {
        productCards.forEach(function (card) {
            const button = card.querySelector('[data-add-product]');
            if (!button) {
                return;
            }

            const item = cart.get(button.dataset.id);
            const quantity = item ? item.quantity : 0;
            const stock = Number.parseInt(button.dataset.stock, 10);
            button.disabled = isSubmitting || stock <= 0 || quantity >= stock;
            button.textContent = quantity >= stock && stock > 0 ? 'В корзине всё количество' : 'Добавить';
        });
    };

    const refreshLoyaltyQuote = async function () {
        const requestNumber = ++quoteRequest;
        if (!loyaltyPanel || !customerSelect || !customerSelect.value || cart.size === 0) {
            if (loyaltyPanel) {
                loyaltyPanel.hidden = true;
            }
            if (redeemPointsInput) {
                redeemPointsInput.value = '0';
            }
            return;
        }

        loyaltyPanel.hidden = false;
        if (loyaltyError) {
            loyaltyError.hidden = true;
            loyaltyError.textContent = '';
        }
        const payload = {
            customer_id: Number.parseInt(customerSelect.value, 10),
            items: Array.from(cart.entries()).map(function (entry) {
                return {
                    product_id: Number.parseInt(entry[0], 10),
                    quantity: entry[1].quantity
                };
            })
        };
        if (posPage && posPage.dataset.branchId) {
            payload.branch_id = Number.parseInt(posPage.dataset.branchId, 10);
        }

        try {
            const response = await fetch(posPage.dataset.loyaltyQuoteUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || 'Не удалось рассчитать бонусы.');
            }
            if (requestNumber !== quoteRequest) {
                return;
            }
            loyaltyBalance.textContent = String(result.available_points);
            loyaltyMaximum.textContent = String(result.maximum_redemption);
            redeemPointsInput.max = String(result.maximum_redemption);
        } catch (error) {
            if (requestNumber !== quoteRequest) {
                return;
            }
            loyaltyBalance.textContent = '—';
            loyaltyMaximum.textContent = '—';
            if (loyaltyError) {
                loyaltyError.hidden = false;
                loyaltyError.textContent = error.message;
            }
        }
    };

    const renderCart = function () {
        cartContainer.replaceChildren();
        let total = 0;

        cart.forEach(function (item, id) {
            const row = document.createElement('article');
            row.className = 'pos-cart-item';

            const description = document.createElement('div');
            description.className = 'pos-cart-description';

            const name = document.createElement('strong');
            name.textContent = item.name;
            const sku = document.createElement('span');
            sku.textContent = item.sku;
            const lineTotal = document.createElement('span');
            lineTotal.className = 'pos-cart-line-total';
            lineTotal.textContent = formatAmount(item.price * item.quantity);
            description.append(name, sku, lineTotal);

            const controls = document.createElement('div');
            controls.className = 'pos-quantity-controls';
            const decrease = document.createElement('button');
            decrease.type = 'button';
            decrease.textContent = '−';
            decrease.setAttribute('aria-label', 'Уменьшить количество: ' + item.name);
            decrease.disabled = isSubmitting || item.quantity <= 1;
            decrease.addEventListener('click', function () {
                if (item.quantity > 1) {
                    item.quantity -= 1;
                    renderCart();
                }
            });

            const quantity = document.createElement('span');
            quantity.className = 'pos-quantity';
            quantity.textContent = String(item.quantity);

            const increase = document.createElement('button');
            increase.type = 'button';
            increase.textContent = '+';
            increase.setAttribute('aria-label', 'Увеличить количество: ' + item.name);
            increase.disabled = isSubmitting || item.quantity >= item.stock;
            increase.addEventListener('click', function () {
                if (item.quantity < item.stock) {
                    item.quantity += 1;
                    renderCart();
                }
            });

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'pos-remove-button';
            remove.textContent = 'Удалить';
            remove.disabled = isSubmitting;
            remove.addEventListener('click', function () {
                cart.delete(id);
                renderCart();
            });
            controls.append(decrease, quantity, increase, remove);

            row.append(description, controls);
            cartContainer.append(row);
            total += item.price * item.quantity;
        });

        emptyCart.hidden = cart.size > 0;
        totalElement.textContent = formatAmount(total);
        clearButton.disabled = isSubmitting || cart.size === 0;
        if (checkoutButton) {
            checkoutButton.disabled = isSubmitting || cart.size === 0;
        }
        updateCatalogButtons();
        refreshLoyaltyQuote();
    };

    productCards.forEach(function (card) {
        const button = card.querySelector('[data-add-product]');
        if (!button) {
            return;
        }

        button.addEventListener('click', function () {
            const id = button.dataset.id;
            const stock = Number.parseInt(button.dataset.stock, 10);
            const price = Number.parseFloat(button.dataset.price);
            if (!id || !Number.isInteger(stock) || stock < 1 || !Number.isFinite(price) || price < 0) {
                return;
            }

            const item = cart.get(id);
            if (item) {
                if (item.quantity < item.stock) {
                    item.quantity += 1;
                }
            } else {
                cart.set(id, {
                    name: button.dataset.name || '',
                    sku: button.dataset.sku || '',
                    price: price,
                    stock: stock,
                    quantity: 1
                });
            }
            renderCart();
        });
    });

    if (searchInput) {
        searchInput.addEventListener('input', function () {
            const query = searchInput.value.trim().toLocaleLowerCase('ru');
            let visibleCount = 0;
            productCards.forEach(function (card) {
                const matches = card.dataset.search.toLocaleLowerCase('ru').includes(query);
                card.hidden = !matches;
                if (matches) {
                    visibleCount += 1;
                }
            });
            if (noResults) {
                noResults.hidden = visibleCount > 0;
            }
        });
    }

    clearButton.addEventListener('click', function () {
        if (isSubmitting) {
            return;
        }
        cart.clear();
        renderCart();
    });

    if (customerSelect) {
        customerSelect.addEventListener('change', refreshLoyaltyQuote);
    }

    if (checkoutButton && posPage) {
        checkoutButton.addEventListener('click', async function () {
            if (isSubmitting || cart.size === 0) {
                return;
            }

            const payload = {
                items: Array.from(cart.entries()).map(function (entry) {
                    return {
                        product_id: Number.parseInt(entry[0], 10),
                        quantity: entry[1].quantity
                    };
                }),
                payment_method: paymentSelect ? paymentSelect.value : ''
            };
            if (redeemPointsInput && redeemPointsInput.value) {
                const requestedPoints = Number(redeemPointsInput.value);
                if (!Number.isSafeInteger(requestedPoints) || requestedPoints < 0) {
                    if (feedback) {
                        feedback.hidden = false;
                        feedback.className = 'pos-feedback is-error';
                        feedback.textContent = 'Количество списываемых баллов должно быть целым неотрицательным числом.';
                    }
                    return;
                }
                payload.redeem_points = requestedPoints;
            }
            if (posPage.dataset.branchId) {
                payload.branch_id = Number.parseInt(posPage.dataset.branchId, 10);
            }
            if (customerSelect && customerSelect.value) {
                payload.customer_id = Number.parseInt(customerSelect.value, 10);
            }
            isSubmitting = true;
            if (feedback) {
                feedback.hidden = false;
                feedback.className = 'pos-feedback is-pending';
                feedback.textContent = 'Оформляем продажу…';
            }

            checkoutButton.textContent = 'Оформление…';
            renderCart();

            try {
                const response = await fetch(posPage.dataset.checkoutUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                let result = {};
                try {
                    result = await response.json();
                } catch (_error) {
                    result = {};
                }

                if (!response.ok) {
                    throw new Error(result.error || 'Не удалось оформить продажу. Проверьте данные и повторите попытку.');
                }

                (result.stock || []).forEach(function (stockRow) {
                    const card = productCards.find(function (productCard) {
                        const addButton = productCard.querySelector('[data-add-product]');
                        return addButton && Number.parseInt(addButton.dataset.id, 10) === stockRow.product_id;
                    });
                    if (!card) {
                        return;
                    }

                    const addButton = card.querySelector('[data-add-product]');
                    const stockDisplay = card.querySelector('[data-stock-display]');
                    addButton.dataset.stock = String(stockRow.stock_quantity);
                    if (stockDisplay) {
                        stockDisplay.textContent = String(stockRow.stock_quantity);
                    }
                    const stockLabel = card.querySelector('.pos-stock');
                    if (stockLabel) {
                        stockLabel.classList.toggle('is-empty', stockRow.stock_quantity === 0);
                    }
                });

                cart.clear();
                isSubmitting = false;
                renderCart();
                if (feedback) {
                    feedback.className = 'pos-feedback is-success';
                    feedback.replaceChildren(
                        document.createTextNode(
                            'Продажа №' + result.sale_id + ' оформлена. Оплачено: ' +
                            result.amount_paid + '. Списано: ' + result.redeemed_points +
                            ', начислено: ' + result.earned_points + ' баллов. '
                        )
                    );
                    const receiptLink = document.createElement('a');
                    receiptLink.className = 'pos-receipt-link';
                    receiptLink.href = result.receipt_url;
                    receiptLink.textContent = 'Посмотреть чек';
                    feedback.append(receiptLink);
                }
            } catch (error) {
                isSubmitting = false;
                if (feedback) {
                    feedback.className = 'pos-feedback is-error';
                    feedback.textContent = error.message || 'Произошла ошибка при оформлении продажи.';
                }
                renderCart();
            } finally {
                checkoutButton.textContent = 'Оформить продажу';
            }
        });
    }
});
