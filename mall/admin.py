from django.contrib import admin
from .models import (
    Category,
    Product,
    Order,
    OrderItem,
    Review,
    Branch,
    PaymentSettings,
    WishlistItem,
    ProductImage,
    PromoCode,
    OrderNote,
    Notification,
    OrderFeedback,
    ProductUpload,
    ProductUploadItem,
    UploadedProductImage,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}
    list_display = ['name', 'slug']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}
    list_display  = ['name', 'category', 'price', 'stock', 'available', 'image_preview']
    list_filter   = ['available', 'category']
    list_editable = ['price', 'stock', 'available']
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'slug', 'category', 'price', 'stock', 'available', 'description')
        }),
        ('Product Image', {
            'fields': ('image', 'image_url'),
            'description': 'Upload an image OR paste an image URL from the web (e.g. from imgbb.com, Cloudinary, etc.)'
        }),
    )

    def image_preview(self, obj):
        from django.utils.html import format_html
        url = obj.image.url if obj.image else obj.image_url
        if url:
            return format_html('<img src="{}" height="50" style="border-radius:4px;object-fit:cover;"/>', url)
        return "No image"
    image_preview.short_description = "Preview"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display  = ['id', 'user', 'full_name', 'branch', 'region', 'status', 'shipping_fee', 'total_price', 'paid', 'created']
    list_filter   = ['status', 'paid', 'region', 'branch']
    inlines       = [OrderItemInline]


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display  = ['name', 'region', 'city', 'phone', 'is_active']
    list_filter   = ['region', 'is_active']
    list_editable = ['is_active']
    search_fields = ['name', 'city', 'address']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['user', 'product', 'rating', 'created']

@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'discount_type', 'discount_value', 'times_used', 'max_uses', 'is_active', 'valid_until']
    list_filter  = ['is_active', 'discount_type']
    search_fields = ['code']


@admin.register(PaymentSettings)
class PaymentSettingsAdmin(admin.ModelAdmin):
    list_display = ['get_icon_display', 'get_provider_display', 'account_name', 'account_number', 'is_active', 'updated_at']
    list_filter  = ['is_active', 'provider', 'created_at']
    search_fields = ['account_name', 'account_number']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Payment Method Details', {
            'fields': ('provider', 'account_name', 'account_number', 'icon')
        }),
        ('Customer Instructions', {
            'fields': ('instructions',),
            'description': 'Instructions displayed to customers at checkout'
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Audit Trail', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_icon_display(self, obj):
        return f"{obj.get_icon()} {obj.get_provider_display()}"
    get_icon_display.short_description = "Payment Method"

@admin.register(OrderNote)
class OrderNoteAdmin(admin.ModelAdmin):
    list_display = ['order', 'staff', 'created']
    raw_id_fields = ['order']

@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ['user', 'product', 'added']

@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ['product', 'sort_order', 'alt_text', 'created']


@admin.register(ProductUploadItem)
class ProductUploadItemAdmin(admin.ModelAdmin):
    list_display = ['product_name', 'upload', 'status', 'approved_at', 'created_at']
    list_filter = ['status']
    search_fields = ['product_name', 'sku', 'category_name']
    readonly_fields = ['approved_at', 'created_at']


@admin.register(ProductUpload)
class ProductUploadAdmin(admin.ModelAdmin):
    list_display = ['id', 'officer', 'status', 'total_items', 'approved_items', 'rejected_items', 'reviewed_by', 'reviewed_at', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['officer__username', 'reviewed_by__username']
    readonly_fields = ['reviewed_at', 'created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('officer', 'csv_file', 'status', 'total_items', 'approved_items', 'rejected_items')}),
        ('Review', {'fields': ('reviewed_by', 'reviewed_at', 'review_notes')}),
    )


@admin.register(UploadedProductImage)
class UploadedProductImageAdmin(admin.ModelAdmin):
    list_display = ['upload_item', 'sort_order', 'image']
    list_filter = ['upload_item__status']
    search_fields = ['upload_item__product_name']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'notif_type', 'title', 'is_read', 'created']
    list_filter  = ['notif_type', 'is_read']


@admin.register(OrderFeedback)
class OrderFeedbackAdmin(admin.ModelAdmin):
    list_display = ['order', 'user', 'nps_score', 'delivery_rating', 'packaging_rating', 'service_rating', 'created']
    list_filter  = ['nps_score']
    readonly_fields = ['order', 'user', 'created']


from .models import OfficerUploadRequest


@admin.register(OfficerUploadRequest)
class OfficerUploadRequestAdmin(admin.ModelAdmin):
    list_display = ['officer', 'status', 'amount', 'amount_paid', 'decided_by', 'decided_at', 'created']
    list_filter  = ['status']
    search_fields = ['officer__username', 'officer__first_name', 'officer__last_name']
    readonly_fields = ['payment_reference', 'amount_paid', 'created', 'updated']
