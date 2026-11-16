USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[member_match_queue]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[member_match_queue](
	[queue_id] [uniqueidentifier] NOT NULL,
	[run_id] [uniqueidentifier] NOT NULL,
	[candidate_member_id] [uniqueidentifier] NULL,
	[incoming_name] [nvarchar](200) NULL,
	[incoming_dob] [date] NULL,
	[incoming_gender] [nvarchar](1) NULL,
	[similarity_score] [float] NULL,
	[status] [nvarchar](20) NOT NULL,
	[reviewed_by] [nvarchar](100) NULL,
	[reviewed_at] [datetime2](7) NULL,
	[created_at] [datetime2](7) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[queue_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[member_match_queue] ADD  DEFAULT ('pending') FOR [status]
GO
ALTER TABLE [dbo].[member_match_queue] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
ALTER TABLE [dbo].[member_match_queue]  WITH CHECK ADD FOREIGN KEY([candidate_member_id])
REFERENCES [dbo].[member_identity] ([member_id])
GO
ALTER TABLE [dbo].[member_match_queue]  WITH CHECK ADD  CONSTRAINT [FK_member_match_queue_run] FOREIGN KEY([run_id])
REFERENCES [dbo].[run] ([run_id])
GO
ALTER TABLE [dbo].[member_match_queue] CHECK CONSTRAINT [FK_member_match_queue_run]
GO
