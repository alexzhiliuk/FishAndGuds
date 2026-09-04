"""Add admin-managed restaurant booking fallback and phone.

Revision ID: 0004_restaurant_channels
Revises: 0003_registration_preferences
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_restaurant_channels"
down_revision = "0003_registration_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "restaurants", sa.Column("booking_url", sa.String(500), nullable=True)
    )
    op.add_column(
        "restaurants", sa.Column("contact_phone", sa.String(32), nullable=True)
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE restaurants
            SET booking_url = COALESCE(
                    booking_url,
                    'https://473113.restoplace.ws/?address=b10a3a9772e9aa1942e9&nostep=1'
                ),
                contact_phone = COALESCE(contact_phone, '+78126797872')
            WHERE name = 'Бистро Рыба и гады'
               OR address LIKE '%Итальянск%'
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE restaurants
            SET delivery_url = NULL
            WHERE name = 'Бистро Рыба и гады'
               OR address LIKE '%Итальянск%'
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE restaurants
            SET booking_url = COALESCE(
                    booking_url,
                    'https://473113.restoplace.ws/?address=113df861c6829a9c32ff&nostep=1'
                ),
                delivery_url = COALESCE(
                    delivery_url,
                    'https://eda.yandex.ru/restaurant/ryba_i_gady'
                ),
                reviews_url = COALESCE(
                    reviews_url,
                    'https://yandex.ru/maps/org/ryba_i_gady/88547327027/reviews/'
                ),
                contact_phone = COALESCE(contact_phone, '+79818977766')
            WHERE name = 'Рыба и гады'
               OR address LIKE '%Большая Конюшенная%'
            """
        )
    )


def downgrade():
    op.drop_column("restaurants", "contact_phone")
    op.drop_column("restaurants", "booking_url")
