package catalog

// Category decides which specs type a product carries.
type Category string

const (
	CategoryElectronics Category = "electronics"
	CategoryBooks       Category = "books"
	CategoryFashion     Category = "fashion"
)

// Product is a catalog entry. Money is kept in RON minor units (bani) so that
// discounts and totals are computed on integers; conversion to other
// currencies happens only when a response is built.
type Product struct {
	ID              int
	Slug            string
	Name            string
	Description     string
	Category        Category
	PriceMinor      int64 // list price in RON bani
	DiscountPercent int   // 0 when the product is not on promotion
	Stock           int
	Rating          float64
	Specs           any // LaptopSpecs, BookSpecs or ApparelSpecs, depending on Category
}

// UnitPrice is the price of one unit in RON bani after the discount.
// Integer division rounds the discounted price down.
func (p Product) UnitPrice() int64 {
	if p.DiscountPercent <= 0 {
		return p.PriceMinor
	}
	return p.PriceMinor * int64(100-p.DiscountPercent) / 100
}

type LaptopSpecs struct {
	// Laptop manufacturer brand (JSON brand), in specs of electronics products.
	Brand string `json:"brand"`
	// Free-text processor description (JSON cpu), e.g. "8-core 3.2 GHz".
	CPU string `json:"cpu"`
	// Memory size in gigabytes (JSON ram_gb).
	RAMGB int `json:"ram_gb"`
	// Storage capacity in gigabytes (JSON storage_gb).
	StorageGB int `json:"storage_gb"`
	// Screen diagonal in inches (JSON screen_inches), a number that may have decimals; a whole value is encoded without a fraction (14).
	ScreenInches float64 `json:"screen_inches"`
	// Warranty length in months (JSON warranty_months).
	WarrantyMonths int `json:"warranty_months"`
}

type BookSpecs struct {
	// Book author name (JSON author), in specs of books products.
	Author string `json:"author"`
	// Page count (JSON pages).
	Pages int `json:"pages"`
	// ISBN as a hyphenated string (JSON isbn), e.g. "978-0-00-000000-2".
	ISBN string `json:"isbn"`
	// Language code of the book (JSON language), e.g. "en".
	Language string `json:"language"`
	// Binding or edition format (JSON format): paperback, hardcover or ebook.
	Format string `json:"format"` // paperback, hardcover or ebook
}

type ApparelSpecs struct {
	// Available sizes as an array of strings (JSON sizes), e.g. ["S","M","L","XL"], in specs of fashion products.
	Sizes []string `json:"sizes"`
	// Colour name (JSON color), e.g. "navy".
	Color string `json:"color"`
	// Fabric composition (JSON material), e.g. "100% cotton".
	Material string `json:"material"`
}
