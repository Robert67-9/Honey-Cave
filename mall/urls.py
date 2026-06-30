from django.urls import path
from . import views, admin_views, google_auth, fulfillment_officer_views, rider_views

urlpatterns = [

    # ─── Custom Admin Panel ───────────────────────────────────────────────────
    path('panel/', admin_views.admin_dashboard, name='admin_dashboard'),
    path('panel/products/', admin_views.admin_products, name='admin_products'),
    path('panel/products/add/', admin_views.admin_product_add, name='admin_product_add'),
    path('panel/products/<int:pk>/edit/', admin_views.admin_product_edit, name='admin_product_edit'),
    path('panel/products/<int:pk>/delete/', admin_views.admin_product_delete, name='admin_product_delete'),
    path('panel/categories/', admin_views.admin_categories, name='admin_categories'),
    path('panel/categories/add/', admin_views.admin_category_add, name='admin_category_add'),
    path('panel/categories/<int:pk>/edit/', admin_views.admin_category_edit, name='admin_category_edit'),
    path('panel/categories/<int:pk>/delete/', admin_views.admin_category_delete, name='admin_category_delete'),
    path('panel/orders/', admin_views.admin_orders, name='admin_orders'),
    path('panel/orders/<int:pk>/', admin_views.admin_order_detail, name='admin_order_detail'),
    path('panel/orders/<int:pk>/delete/', admin_views.admin_order_delete, name='admin_order_delete'),
    path('panel/users/', admin_views.admin_users, name='admin_users'),
    path('panel/users/<int:pk>/', admin_views.admin_user_detail, name='admin_user_detail'),
    path('panel/users/<int:pk>/delete/', admin_views.admin_user_delete, name='admin_user_delete'),
    path('panel/reviews/', admin_views.admin_reviews, name='admin_reviews'),
    path('panel/reviews/<int:pk>/delete/', admin_views.admin_review_delete, name='admin_review_delete'),
    path('panel/branches/', admin_views.admin_branches, name='admin_branches'),
    path('panel/branches/add/', admin_views.admin_branch_add, name='admin_branch_add'),
    path('panel/branches/<int:pk>/edit/', admin_views.admin_branch_edit, name='admin_branch_edit'),
    path('panel/branches/<int:pk>/delete/', admin_views.admin_branch_delete, name='admin_branch_delete'),


    path('', views.home, name='home'),

    # Products
    path('products/', views.product_list, name='product_list'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),

    # Cart
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:product_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/remove/<int:product_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('cart/update/<int:product_id>/', views.update_cart, name='update_cart'),

    # Shipping
    path('branches/', views.branches, name='branches'),
    # Location / nearest branch
    path('api/save-location/', views.save_location, name='save_location'),
    path('api/branches/', views.branches_by_region_api, name='branches_api'),
    path('api/payment-methods/', views.payment_methods_api, name='payment_methods_api'),
    path('api/paystack/verify/', views.paystack_verify, name='paystack_verify'),
    # Paystack dashboard "Callback URL" — GET landing page after redirect-based
    # payments (mobile money / mobile browsers). Set in Paystack dashboard as:
    #   https://<your-domain>/payment/callback/
    path('payment/callback/', views.paystack_callback, name='paystack_callback'),

    path('api/delivery-fee/', views.delivery_fee_api, name='delivery_fee_api'),
    path('api/reverse-geocode/', views.reverse_geocode_api, name='reverse_geocode_api'),
    path('api/paystack/webhook/', views.paystack_webhook, name='paystack_webhook'),
    # Generic provider webhook — adapter-routed. New gateways add a row in
    # PaymentSettings and point their dashboard at /api/payments/<slug>/webhook/.
    path('api/payments/<slug:provider>/webhook/', views.provider_webhook, name='provider_webhook'),

    # Checkout
    path('checkout/', views.checkout, name='checkout'),
    path('order/<int:order_id>/confirmation/', views.order_confirmation, name='order_confirmation'),
    path('api/order/<int:order_id>/status/',   views.order_status_json,  name='order_status_json'),
    path('my-orders/', views.my_orders, name='my_orders'),
    path('order-history/', views.order_history, name='order_history'),

    # Auth
    path('register/', views.register_view, name='register'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('resend-otp/', views.resend_otp, name='resend_otp'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('reset-password/', views.reset_password, name='reset_password'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    # Sign in with Google
    path('auth/google/start/',    google_auth.google_login_start,    name='google_login_start'),
    path('auth/google/callback/', google_auth.google_login_callback, name='google_login_callback'),
    path('profile/', views.profile, name='profile'),
    path('select-branch/', views.select_branch, name='select_branch'),
    # SEC-04: Dedicated admin login — separate rate limit scope from customer login
    path('panel/login/', views.admin_login_view, name='admin_login'),

    # Admin — Payment Settings
    path('panel/payment-settings/', admin_views.admin_payment_settings, name='admin_payment_settings'),
    path('panel/payment-settings/add/', admin_views.admin_payment_settings_add, name='admin_payment_settings_add'),
    path('panel/payment-settings/<int:pk>/edit/', admin_views.admin_payment_settings_edit, name='admin_payment_settings_edit'),
    path('panel/payment-settings/<int:pk>/delete/', admin_views.admin_payment_settings_delete, name='admin_payment_settings_delete'),
    path('panel/payment-settings/<int:pk>/test/', admin_views.admin_payment_test, name='admin_payment_test'),

    # Info
    path('contact/', views.contact, name='contact'),
    path('terms/', views.terms, name='terms'),
    path('privacy/', views.privacy, name='privacy'),

    # Reviews
    path('reviews/<int:review_id>/helpful/', views.mark_review_helpful, name='mark_review_helpful'),
    path('orders/<int:order_id>/review/', views.order_review, name='order_review'),
    path('panel/reviews/<int:pk>/toggle/', admin_views.admin_review_toggle, name='admin_review_toggle'),

    # FEAT-01: Wishlist
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.wishlist_toggle, name='wishlist_toggle'),
    path('wishlist/add-all-to-cart/', views.wishlist_add_all_to_cart, name='wishlist_add_all_to_cart'),
    path('api/wishlist/count/', views.wishlist_count, name='wishlist_count'),

    # FEAT-05: Order Cancellation
    path('orders/<int:order_id>/cancel/', views.cancel_order, name='cancel_order'),

    # FEAT-07: Promo Code AJAX
    path('api/apply-promo/', views.apply_promo_code, name='apply_promo_code'),
    path('api/cart/branch-check/', views.cart_branch_check, name='cart_branch_check'),

    # FEAT-NOTIF: Customer Notifications
    path('notifications/', views.notifications_view, name='notifications'),
    path('notifications/mark-read/', views.mark_notifications_read, name='mark_notifications_read'),
    path('notifications/count/', views.notifications_count, name='notifications_count'),

    # FEAT-03: CSV Exports
    path('panel/export/orders/', admin_views.admin_export_orders_csv, name='admin_export_orders'),
    path('panel/export/products/', admin_views.admin_export_products_csv, name='admin_export_products'),
    path('panel/export/users/', admin_views.admin_export_users_csv, name='admin_export_users'),

    # FEAT-06: Product Image Gallery
    path('panel/products/<int:pk>/gallery/', admin_views.admin_product_gallery, name='admin_product_gallery'),

    # FEAT-07: Promo Code Admin
    path('panel/promo-codes/', admin_views.admin_promo_codes, name='admin_promo_codes'),
    path('panel/promo-codes/add/', admin_views.admin_promo_code_add, name='admin_promo_code_add'),
    path('panel/promo-codes/<int:pk>/edit/', admin_views.admin_promo_code_edit, name='admin_promo_code_edit'),
    path('panel/promo-codes/<int:pk>/delete/', admin_views.admin_promo_code_delete, name='admin_promo_code_delete'),

    # FEAT-NOTIF: Admin Notifications
    path('panel/notifications/', admin_views.admin_notifications, name='admin_notifications'),

    # FEAT: Order Feedback
    path('orders/<int:order_id>/feedback/', views.order_feedback, name='order_feedback'),
    path('panel/feedback/', admin_views.admin_feedback, name='admin_feedback'),

    # FEAT: AI Features
    path('ai/chat/', views.ai_chat, name='ai_chat'),
    path('api/ai/chat-context/', views.ai_chat_context, name='ai_chat_context'),
    path('api/ai/recommendations/', views.ai_recommendations, name='ai_recommendations'),
    path('api/ai/review-summary/<slug:product_slug>/', views.ai_review_summary, name='ai_review_summary'),
    path('panel/ai-insights/', admin_views.admin_ai_insights, name='admin_ai_insights'),

    # FEAT: Rider Delivery System
    path('delivery/<str:token>/', views.rider_delivery_portal, name='rider_delivery_portal'),
    # NEW rider portal — phone-OTP login, dashboard, per-order page, history.
    path('rider/login/',  rider_views.rider_login,      name='rider_login'),
    path('rider/verify/', rider_views.rider_verify_otp, name='rider_verify_otp'),
    path('rider/logout/', rider_views.rider_logout,     name='rider_logout'),
    path('rider/',                       rider_views.rider_dashboard, name='rider_dashboard'),
    path('rider/order/<int:order_id>/',  rider_views.rider_order,     name='rider_order'),
    path('rider/history/',               rider_views.rider_history,   name='rider_history'),
    path('orders/<int:order_id>/confirm-delivery/', views.confirm_delivery, name='confirm_delivery'),

    # CSV Import
    path('panel/csv-import/', admin_views.admin_csv_import, name='admin_csv_import'),
    path('panel/csv-import/products/', admin_views.admin_csv_import_products, name='admin_csv_import_products'),
    path('panel/csv-import/branches/', admin_views.admin_csv_import_branches, name='admin_csv_import_branches'),
    path('panel/csv-import/promo-codes/', admin_views.admin_csv_import_promo_codes, name='admin_csv_import_promo_codes'),
    path('panel/csv-import/template/<str:entity>/', admin_views.admin_csv_template_download, name='admin_csv_template_download'),

    # Audit Log
    path('panel/audit-log/', admin_views.admin_audit_log, name='admin_audit_log'),

    # Admin 2FA
    path('panel/2fa/setup/', admin_views.admin_2fa_setup, name='admin_2fa_setup'),
    path('panel/2fa/verify/', admin_views.admin_2fa_verify, name='admin_2fa_verify'),

    # Inventory Dashboard
    path('panel/inventory/', admin_views.admin_inventory, name='admin_inventory'),

    # Promotions (internal banners & sponsored slots)
    path('panel/promotions/', admin_views.admin_promotions, name='admin_promotions'),
    path('panel/promotions/add/', admin_views.admin_promotion_add, name='admin_promotion_add'),
    path('panel/promotions/<int:pk>/edit/', admin_views.admin_promotion_edit, name='admin_promotion_edit'),
    path('panel/promotions/<int:pk>/delete/', admin_views.admin_promotion_delete, name='admin_promotion_delete'),
    path('panel/promotions/<int:pk>/toggle/', admin_views.admin_promotion_toggle, name='admin_promotion_toggle'),
    path('promo/<int:pk>/', views.promotion_click, name='promotion_click'),

    # Site Settings (contact info & socials)
    path('panel/site-settings/', admin_views.admin_site_settings, name='admin_site_settings'),

    # Fulfillment Officer portal — branch staff log in to process orders
    path('officer/login/',  fulfillment_officer_views.fulfillment_officer_login,  name='fulfillment_officer_login'),
    path('officer/logout/', fulfillment_officer_views.fulfillment_officer_logout, name='fulfillment_officer_logout'),
    path('officer/',        fulfillment_officer_views.fulfillment_officer_dashboard, name='fulfillment_officer_dashboard'),
    path('officer/history/', fulfillment_officer_views.fulfillment_officer_history,  name='fulfillment_officer_history'),
    path('officer/order/<int:pk>/', fulfillment_officer_views.fulfillment_officer_order, name='fulfillment_officer_order'),
    path('officer/branches/',         fulfillment_officer_views.fulfillment_officer_branches,         name='fulfillment_officer_branches'),
    path('officer/branches/request/', fulfillment_officer_views.fulfillment_officer_request_branch,   name='fulfillment_officer_request_branch'),
    path('officer/riders/',           fulfillment_officer_views.fulfillment_officer_riders,           name='fulfillment_officer_riders'),
    path('officer/riders/add/',       fulfillment_officer_views.fulfillment_officer_rider_add,        name='fulfillment_officer_rider_add'),
    path('officer/product-upload/',   fulfillment_officer_views.officer_product_upload,     name='officer_product_upload'),
    path('officer/upload-access/',     fulfillment_officer_views.officer_upload_access,      name='officer_upload_access'),
    path('officer/upload-access/request/', fulfillment_officer_views.officer_request_upload_access, name='officer_request_upload_access'),
    path('officer/upload-access/verify/',  fulfillment_officer_views.officer_upload_pay_verify,     name='officer_upload_pay_verify'),
    path('officer/my-products/',      fulfillment_officer_views.officer_my_products,        name='officer_my_products'),
    path('officer/csv-upload/',       fulfillment_officer_views.officer_csv_upload,         name='officer_csv_upload'),

    # Admin fulfillment officer management — admin registers / edits fulfillment officer accounts
    path('panel/fulfillment-officers/',                       admin_views.admin_fulfillment_officers,           name='admin_fulfillment_officers'),
    path('panel/upload-requests/',                            admin_views.admin_upload_requests,                name='admin_upload_requests'),
    path('panel/fulfillment-officers/add/',                   admin_views.admin_fulfillment_officer_add,        name='admin_fulfillment_officer_add'),
    path('panel/fulfillment-officers/<int:pk>/edit/',         admin_views.admin_fulfillment_officer_edit,       name='admin_fulfillment_officer_edit'),
    path('panel/fulfillment-officers/<int:pk>/upload-access/', admin_views.admin_officer_upload_access_action,  name='admin_officer_upload_access_action'),
    path('panel/fulfillment-officers/<int:pk>/reset/',        admin_views.admin_fulfillment_officer_reset_password, name='admin_fulfillment_officer_reset_password'),
    path('panel/fulfillment-officers/<int:pk>/deactivate/',   admin_views.admin_fulfillment_officer_deactivate, name='admin_fulfillment_officer_deactivate'),

    # Admin rider roster — manage the persistent rider list, verify auto-drafts
    path('panel/riders/',                                     admin_views.admin_riders,         name='admin_riders'),
    path('panel/riders/add/',                                 admin_views.admin_rider_form,     name='admin_rider_add'),
    path('panel/riders/<int:pk>/edit/',                       admin_views.admin_rider_form,     name='admin_rider_edit'),
    path('panel/riders/<int:pk>/verify/',                     admin_views.admin_rider_verify,       name='admin_rider_verify'),
    path('panel/riders/<int:pk>/deactivate/',                 admin_views.admin_rider_deactivate,   name='admin_rider_deactivate'),
    path('panel/riders/<int:pk>/reactivate/',                 admin_views.admin_rider_reactivate,   name='admin_rider_reactivate'),

    # Admin branch-request approval queue
    path('panel/branch-requests/',                            admin_views.admin_branch_requests,         name='admin_branch_requests'),
    path('panel/branch-requests/<int:pk>/decide/',            admin_views.admin_branch_request_decide,   name='admin_branch_request_decide'),
]
