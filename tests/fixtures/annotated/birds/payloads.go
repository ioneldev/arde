package birds

// Sighting is one bird seen at one place. Details holds a Nest or a Flock, depending on the bird.
type Sighting struct {
	ID      int    `json:"id"`
	Species string `json:"species"`
	Note    string `json:"note,omitempty" binding:"optional"`
	Details any    `json:"details" oneOf:"birds.Nest,birds.Flock"`
}

// Nest is what was seen of a bird on its nest.
type Nest struct {
	Eggs int `json:"eggs"`
}

// Flock is what was seen of a flock in the air.
type Flock struct {
	Size    int    `json:"size"`
	Heading string `json:"heading"`
}

// Problem is the reply when a sighting cannot be given.
type Problem struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}
