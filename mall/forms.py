import re
from django import forms
from django.contrib.auth.forms import UserCreationForm, SetPasswordForm
from django.contrib.auth.models import User
from .models import Order, Review, ReviewHelpful, REGION_CHOICES, PaymentSettings, UserProfile, PromoCode


class PromoCodeForm(forms.Form):
    code = forms.CharField(
        max_length=50, label='Promo Code',
        widget=forms.TextInput(attrs={'placeholder': 'Enter promo code'}),
    )

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class RegisterForm(UserCreationForm):
    email      = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=50, required=True)
    last_name  = forms.CharField(max_length=50, required=True)
    phone      = forms.CharField(
        max_length=20, required=False,
        help_text='Optional — e.g. 0244123456',
    )

    class Meta:
        model  = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'password1', 'password2']

    # ── Duplicate e-mail check ────────────────────────────────────────────────
    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'An account with this email address already exists.'
            )
        return email

    # ── Phone: digits, spaces, +, hyphens only ────────────────────────────────
    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and not re.match(r'^[\d\s\+\-\(\)]{7,20}$', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    # ── Username: no special characters that could cause issues ──────────────
    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not re.match(r'^[\w.@+-]{3,150}$', username):
            raise forms.ValidationError(
                'Username may only contain letters, digits, and @/./+/-/_ (3–150 chars).'
            )
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email     = self.cleaned_data['email'].lower()
        user.is_active = False   # inactive until OTP verified
        if commit:
            user.save()
            UserProfile.objects.get_or_create(
                user=user,
                defaults={'phone': self.cleaned_data.get('phone', '')},
            )
        return user


class ProfileUpdateForm(forms.ModelForm):
    """Allow users to update their first/last name and phone from the profile page."""
    first_name = forms.CharField(max_length=50, required=False)
    last_name  = forms.CharField(max_length=50, required=False)

    class Meta:
        model  = UserProfile
        fields = ['phone']

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and not re.match(r'^[\d\s\+\-\(\)]{7,20}$', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone


class OTPVerifyForm(forms.Form):
    otp = forms.CharField(
        max_length=6, min_length=6, required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Enter 6-digit code',
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
        }),
        label='Verification Code',
    )

    def clean_otp(self):
        otp = self.cleaned_data['otp'].strip()
        if not otp.isdigit():
            raise forms.ValidationError('Code must be 6 digits.')
        return otp


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(label='Your account email')

    def clean_email(self):
        return self.cleaned_data['email'].lower().strip()


class ResetPasswordForm(SetPasswordForm):
    pass


class ContactForm(forms.Form):
    name    = forms.CharField(max_length=100, label='Your Name')
    email   = forms.EmailField(label='Your Email')
    subject = forms.CharField(max_length=200)
    message = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 5}),
        max_length=2000,
    )

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if len(name) < 2:
            raise forms.ValidationError('Please enter your name.')
        return name

    def clean_message(self):
        msg = self.cleaned_data['message'].strip()
        if len(msg) < 10:
            raise forms.ValidationError('Message too short — please give us some detail.')
        return msg


class CheckoutForm(forms.ModelForm):
    fulfillment_type = forms.ChoiceField(
        choices=[
            ('pickup',   'Branch Pickup — collect at your chosen branch'),
            ('delivery', 'Home Delivery — we bring it to your door'),
        ],
        widget=forms.RadioSelect,
        initial='pickup',
        label='How would you like to receive your order?',
    )
    delivery_address = forms.CharField(
        required=False,
        label='Delivery Address',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Street / neighbourhood / landmark — be as specific as possible',
        }),
        help_text='Required for Home Delivery.',
    )

    # Optional landmark — Ghanaian addresses lean on these
    # ("behind the blue water tank, opposite the church"). Riders rely on
    # them more than on the actual address text, so we surface a dedicated
    # field instead of hoping the customer puts the landmark in the address.
    delivery_landmark = forms.CharField(
        required=False,
        max_length=200,
        label='Landmark (optional but very helpful)',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. behind the blue water tank, near Ghana Methodist Church',
        }),
        help_text='A nearby reference point to help the rider find you.',
    )

    # Hidden — populated by the "Use my current location" button via JS.
    # Validated server-side to be inside Ghana's bounding box. We never
    # require these (some browsers block geolocation, some customers refuse
    # the permission) but when present they enable the rider's
    # tap-to-navigate button.
    delivery_lat = forms.FloatField(required=False, widget=forms.HiddenInput())
    delivery_lng = forms.FloatField(required=False, widget=forms.HiddenInput())

    payment_reference = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model   = Order
        fields  = ['full_name', 'email', 'phone', 'address', 'city', 'zip_code',
                   'fulfillment_type', 'delivery_address', 'delivery_landmark',
                   'delivery_lat', 'delivery_lng', 'payment_reference']
        widgets = {'address': forms.Textarea(attrs={'rows': 2})}

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and not re.match(r'^[\d\s\+\-\(\)]{7,20}$', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('fulfillment_type') == 'delivery' and not cleaned.get('delivery_address', '').strip():
            self.add_error('delivery_address', 'Please enter your delivery address.')

        # Validate captured GPS coordinates if present. Reject anything
        # outside Ghana — the user almost certainly wasn't in Argentina
        # when checking out at Honey Cave Market, so a coord that far out
        # is a sign of either spoofing or a broken geolocation source.
        # Rather than block the order, we silently strip the bad coords
        # and fall back to the typed address.
        lat = cleaned.get('delivery_lat')
        lng = cleaned.get('delivery_lng')
        if lat is not None and lng is not None:
            in_ghana = (-3.5 <= lng <= 1.5) and (4.5 <= lat <= 11.5)
            if not in_ghana:
                cleaned['delivery_lat'] = None
                cleaned['delivery_lng'] = None
        else:
            # Both must be present for either to be useful — drop a
            # half-set pair to avoid a non-NULL/NULL split in the DB.
            cleaned['delivery_lat'] = None
            cleaned['delivery_lng'] = None

        return cleaned


class ReviewForm(forms.ModelForm):
    RATING_CHOICES = [(5,'5'), (4,'4'), (3,'3'), (2,'2'), (1,'1')]
    rating = forms.ChoiceField(
        choices=RATING_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'star-radio'}),
        label='Your Rating',
    )

    class Meta:
        model   = Review
        fields  = ['rating', 'title', 'comment']
        widgets = {
            'title':   forms.TextInput(attrs={'placeholder': 'Summarise your experience (optional)'}),
            'comment': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Tell other shoppers about this product — quality, delivery, value...',
            }),
        }
        labels = {
            'title':   'Review Headline',
            'comment': 'Your Review',
        }

    def clean_rating(self):
        val = self.cleaned_data['rating']
        try:
            val = int(val)
            if val not in range(1, 6):
                raise forms.ValidationError('Rating must be between 1 and 5.')
        except (TypeError, ValueError):
            raise forms.ValidationError('Invalid rating.')
        return val

    def clean_comment(self):
        comment = self.cleaned_data['comment'].strip()
        if len(comment) < 5:
            raise forms.ValidationError('Please write at least a brief comment.')
        if len(comment) > 2000:
            raise forms.ValidationError('Review is too long (max 2000 characters).')
        return comment


class PaymentSettingsForm(forms.ModelForm):
    class Meta:
        model  = PaymentSettings
        fields = ['provider', 'account_name', 'account_number', 'extra_account', 'icon', 'instructions', 'is_active']
        widgets = {
            'account_name':   forms.TextInput(attrs={'placeholder': 'e.g. Market Payments'}),
            'account_number': forms.TextInput(attrs={'placeholder': 'pk_live_... (Paystack Public Key)'}),
            'extra_account':  forms.TextInput(attrs={'placeholder': 'Account Number (if required)'}),
            'icon':           forms.TextInput(attrs={'placeholder': '💳 (emoji or leave empty for default)'}),
            'instructions':   forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional message shown to customers'}),
        }
        labels = {
            'provider':       'Gateway',
            'account_name':   'Display Name',
            'account_number': 'Public Key',
        }
        help_texts = {
            'provider':      'Which payment gateway this row is for.',
            'extra_account': 'Leave empty for Paystack.',
        }

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get('provider')
        extra = (cleaned.get('extra_account') or '').strip()
        return cleaned

    def clean_icon(self):
        """Auto-fill icon if empty."""
        icon = self.cleaned_data.get('icon', '').strip()
        if not icon:
            icon = '💳'
        return icon


from .models import OrderFeedback

class OrderFeedbackForm(forms.ModelForm):
    NPS_CHOICES = [(i, str(i)) for i in range(0, 11)]
    nps_score = forms.ChoiceField(
        choices=NPS_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'nps-radio'}),
        label='How likely are you to recommend Market? (0 = Not at all, 10 = Definitely)',
    )
    STAR_CHOICES = [(i, '★' * i) for i in range(1, 6)]
    delivery_rating = forms.ChoiceField(
        choices=STAR_CHOICES, widget=forms.RadioSelect(attrs={'class': 'star-radio'}),
        label='Delivery Speed',
    )
    packaging_rating = forms.ChoiceField(
        choices=STAR_CHOICES, widget=forms.RadioSelect(attrs={'class': 'star-radio'}),
        label='Packaging Quality',
    )
    service_rating = forms.ChoiceField(
        choices=STAR_CHOICES, widget=forms.RadioSelect(attrs={'class': 'star-radio'}),
        label='Customer Service',
    )

    class Meta:
        model   = OrderFeedback
        fields  = ['nps_score', 'delivery_rating', 'packaging_rating', 'service_rating',
                   'comment', 'photo_1', 'photo_2', 'photo_3']
        widgets = {
            'comment': forms.Textarea(attrs={
                'rows': 4, 'placeholder': 'Tell us about your experience (optional)…',
                'class': 'form-control',
            }),
            'photo_1': forms.FileInput(attrs={'accept': 'image/*', 'class': 'form-control'}),
            'photo_2': forms.FileInput(attrs={'accept': 'image/*', 'class': 'form-control'}),
            'photo_3': forms.FileInput(attrs={'accept': 'image/*', 'class': 'form-control'}),
        }

    def clean_nps_score(self):
        return int(self.cleaned_data['nps_score'])

    def clean_delivery_rating(self):
        return int(self.cleaned_data['delivery_rating'])

    def clean_packaging_rating(self):
        return int(self.cleaned_data['packaging_rating'])

    def clean_service_rating(self):
        return int(self.cleaned_data['service_rating'])

    def _validate_photo(self, field_name):
        f = self.cleaned_data.get(field_name)
        if f:
            from .security import validate_uploaded_image
            err = validate_uploaded_image(f)
            if err:
                raise forms.ValidationError(err)
        return f

    def clean_photo_1(self): return self._validate_photo('photo_1')
    def clean_photo_2(self): return self._validate_photo('photo_2')
    def clean_photo_3(self): return self._validate_photo('photo_3')

